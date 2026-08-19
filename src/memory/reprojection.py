# src/memory/reprojection.py
from __future__ import annotations

import asyncio
from typing import NamedTuple

from pydantic import BaseModel, Field

from memory.constraint import Applicability, Necessity, Scope, Source
from memory.constraint_store import ConstraintStore
from memory.judge import Judge
from memory.models import Channel, Observation, Tier
from memory.store import ObservationStore

# Mirrors projection._SOURCE_BY_CHANNEL. Imported rather than duplicated would
# be better, but projection imports nothing from here and this module must not
# create a cycle; see _source_for.
_SOURCE_BY_CHANNEL = {
    Channel.PLANNING: Source.USER,
    Channel.REVIEW: Source.USER,
    Channel.CALENDAR: Source.CALENDAR,
}

# How many observations may be in front of the model at once. Re-projection
# fans out over the whole store, so an unbounded gather would open one request
# per observation the moment it starts.
_MAX_CONCURRENT_JUDGEMENTS = 8


class ConstraintChange(BaseModel):
    """What re-projection did to one constraint."""

    uid: str
    name: str
    fields: list[str] = Field(default_factory=list)


class ReprojectionReport(BaseModel):
    """The audit trail for a run that rewrote derived state in place.

    Re-projection touches every constraint in the store, so "it worked" is not
    an observation anyone can make without this. `changed` naming the fields
    is what makes a judgement improvement measurable rather than asserted.
    """

    examined: int = 0
    changed: list[ConstraintChange] = Field(default_factory=list)
    unchanged: int = 0
    skipped: list[tuple[str, str]] = Field(default_factory=list)


class _Judged(NamedTuple):
    tier: object
    necessity: object


async def _judge_all(
    observations: list[Observation], judge: Judge
) -> dict[str, _Judged]:
    """Re-ask the derivable questions for every observation, concurrently.

    Two of the six are re-asked. `anchors` was written into the append-only
    log at ingest and cannot be revised here without violating I2. `meta` and
    `dedup` are admission gates whose answers already decided what got stored;
    re-running them would mean un-storing, which I2 also forbids. And
    `canonicalise` merges rather than derives — see the note on reproject.

    Both questions for every observation go out in one gather rather than one
    gather per question, so the fan-out is over the whole work set instead of
    being serialised into two rounds.
    """
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_JUDGEMENTS)

    async def ask(coro_factory):
        async with semaphore:
            return await coro_factory()

    results = await asyncio.gather(
        *(
            ask(factory)
            for observation in observations
            for factory in (
                lambda o=observation: judge.tier(o),
                lambda o=observation: judge.necessity(o),
            )
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return {
        observation.uid: _Judged(
            tier=results[i * 2], necessity=results[i * 2 + 1]
        )
        for i, observation in enumerate(observations)
    }


def _derive(observations: list[Observation], judgements: dict) -> dict:
    """Combine per-observation judgements into one constraint's fields.

    Every rule here is arithmetic over judgements already made — a max, a
    min, a most-recent — never a fresh reading of the user's text. The model
    decided what each observation means; this decides only how several such
    decisions add up.

    KNOWN FLATTENING: when two observations disagree on a value, the newer one
    wins and the older is invisible in the result. That is the same silent
    fold #137 decided against, and it stays until the edge table lands to hold
    contested values side by side. Provenance survives either way, so the
    disagreement is recoverable from L1 rather than lost.
    """
    ordered = sorted(observations, key=lambda o: o.observed_at)
    newest = ordered[-1]
    newest_j = judgements[newest.uid].tier

    # Tier only ever moves up: one durable statement makes the rule durable,
    # and no later session-tier mention demotes it. Same rule the fold branch
    # in project() follows, for the same reason — demotion would be
    # last-write-wins, which this design rejects.
    tier = (
        Tier.DURABLE
        if any(judgements[o.uid].tier.tier is Tier.DURABLE for o in ordered)
        else Tier.SESSION
    )

    # Applicability from the most recent observation that supplies any. A
    # later restatement that happens to omit the scoping words should not
    # widen a rule back to every day.
    applicability = Applicability()
    for observation in reversed(ordered):
        judgement = judgements[observation.uid].tier
        if judgement.start_date or judgement.end_date or judgement.days_of_week:
            applicability = Applicability(
                start_date=judgement.start_date,
                end_date=judgement.end_date,
                days_of_week=judgement.days_of_week,
            )
            break

    return {
        "name": newest_j.label or newest.text,
        "description": newest.text,
        # Binding once, binding after: a casual later mention of a rule the
        # person stated as a hard boundary does not soften it. Same shape as
        # tier, and for the same reason — softening on the newest observation
        # would be last-write-wins.
        "necessity": Necessity.MUST
        if any(judgements[o.uid].necessity.is_binding for o in ordered)
        else Necessity.SHOULD,
        "scope": Scope.PROFILE if tier is Tier.DURABLE else Scope.SESSION,
        "source": _SOURCE_BY_CHANNEL[newest.channel],
        "tier": tier,
        "applicability": applicability,
        "decay_class": newest_j.decay_class,
        "created_at": ordered[0].observed_at,
        "last_observed_at": ordered[-1].observed_at,
    }


async def reproject(
    observation_store: ObservationStore,
    constraint_store: ConstraintStore,
    judge: Judge,
    *,
    uid: str | None = None,
) -> ReprojectionReport:
    """Re-derive constraints from the observations that produced them (I4).

    Without this, `project()` writes derived fields only on its create branch
    and a fold touches nothing but `last_observed_at` — so every judgement
    improvement reaches only constraints created after it shipped, and a store
    is frozen at the taxonomy of the run that made it.

    Identity is preserved (I3): a constraint keeps the uid it was minted with,
    so anything holding a reference still resolves. Provenance is rewritten to
    exactly the observations used, via replace_links, because re-projection can
    drop an observation as well as keep one.

    This deliberately does NOT re-run canonicalise. Deciding afresh which
    constraints are the same rule is a different operation — it merges and
    splits rows rather than re-deriving their fields — and it belongs to the
    write path (#145). Re-projection that silently re-merged would reshape the
    store on every run, and no report could make that legible.
    """
    targets = (
        [c for c in [constraint_store.get(uid)] if c is not None]
        if uid is not None
        else constraint_store.all()
    )
    report = ReprojectionReport(examined=len(targets))

    for constraint in targets:
        observation_uids = constraint_store.observations_for(constraint.uid)
        if not observation_uids:
            report.skipped.append(
                (constraint.uid, "no provenance: nothing to re-derive from")
            )
            continue

        observations = [observation_store.get(u) for u in observation_uids]
        missing = [
            u for u, o in zip(observation_uids, observations) if o is None
        ]
        if missing:
            # L1 is append-only, so a dangling link means the log and the
            # constraint table came from different stores. Re-deriving from a
            # partial set would quietly produce a different rule.
            report.skipped.append(
                (
                    constraint.uid,
                    f"provenance points at {len(missing)} observation(s) absent "
                    f"from the log: {sorted(missing)}",
                )
            )
            continue

        judgements = await _judge_all(observations, judge)
        derived = _derive(observations, judgements)

        changed_fields = [
            field
            for field, value in derived.items()
            if getattr(constraint, field) != value
        ]
        if not changed_fields:
            report.unchanged += 1
            continue

        for field, value in derived.items():
            setattr(constraint, field, value)
        # No provenance assignment needed: upsert persists
        # source_observation_uids via replace_links, and _row populated that
        # list from the same link table observation_uids was read from, so
        # the set used for derivation is by construction the set persisted.
        constraint_store.upsert(constraint)
        report.changed.append(
            ConstraintChange(
                uid=constraint.uid, name=constraint.name, fields=changed_fields
            )
        )

    return report
