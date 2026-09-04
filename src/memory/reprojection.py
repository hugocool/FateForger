# src/memory/reprojection.py
from __future__ import annotations

import asyncio
from typing import NamedTuple

from pydantic import BaseModel, Field

from memory.anchor_store import AnchorStore
from memory.anchoring import resolve_anchors
from memory.constraint import Applicability, Constraint, Necessity, Scope, Source
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


class ContestedConstraint(BaseModel):
    """A constraint whose prose was left alone because its evidence disagrees."""

    uid: str
    name: str
    observations: int


class ReprojectionReport(BaseModel):
    """The audit trail for a run that rewrote derived state in place.

    Re-projection touches every constraint in the store, so "it worked" is not
    an observation anyone can make without this. `changed` naming the fields
    is what makes a judgement improvement measurable rather than asserted.
    """

    # Whether anything was written. Without it a preview and a real run return
    # the same shape, and a caller cannot tell which it got — which is the
    # advisory-data failure this flag exists to prevent, reproduced in the fix
    # for it.
    applied: bool = False
    examined: int = 0
    changed: list[ConstraintChange] = Field(default_factory=list)
    unchanged: int = 0
    skipped: list[tuple[str, str]] = Field(default_factory=list)
    # Multi-observation constraints whose name and description were preserved
    # rather than re-derived. Reported rather than silent: a caller comparing
    # counts would otherwise read "changed" as "fully re-derived".
    contested: list[ContestedConstraint] = Field(default_factory=list)


class _Judged(NamedTuple):
    tier: object
    necessity: object
    requires_block: object


async def _judge_all(
    observations: list[Observation], judge: Judge, kinds: list[str]
) -> dict[str, _Judged]:
    """Re-ask the derivable questions for every observation, concurrently.

    Three of the seven are re-asked (anchors, meta, dedup, canonicalise stay
    excluded for the reasons already stated). `anchors` was written into the
    append-only log at ingest and cannot be revised here without violating
    I2. `meta` and `dedup` are admission gates whose answers already decided
    what got stored; re-running them would mean un-storing, which I2 also
    forbids. And `canonicalise` merges rather than derives — see the note on
    reproject.

    All three questions for every observation go out in one gather rather
    than one gather per question, so the fan-out is over the whole work set
    instead of being serialised into three rounds.
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
                lambda o=observation: judge.requires_block(o, list(kinds)),
            )
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return {
        observation.uid: _Judged(
            tier=results[i * 3],
            necessity=results[i * 3 + 1],
            requires_block=results[i * 3 + 2],
        )
        for i, observation in enumerate(observations)
    }


def _derive(
    observations: list[Observation],
    judgements: dict,
    existing: Constraint | None = None,
) -> dict:
    """Combine per-observation judgements into one constraint's fields.

    Every rule here is arithmetic over judgements already made — a max, a
    min, a most-recent — never a fresh reading of the user's text. The model
    decided what each observation means; this decides only how several such
    decisions add up.

    `name` and `description` are NOT re-derived when a constraint has more than
    one observation, and that restriction is the whole point. They carry the
    rule's meaning in prose, so taking the newest does not flatten a contested
    *value* — it replaces the rule with whichever statement happened to be
    said last. Measured on the real corpus, that overwrote 8 of 12
    multi-observation constraints: a rule asserting deep-work blocks are two
    hours, supported by eight observations, became "at least 90 minutes" on
    the strength of one later one; a rule about eating three meals a day
    became "Lunch break".

    Every other field here folds across all observations for exactly this
    reason, and the comment below on necessity states the principle —
    softening on the newest observation would be last-write-wins. That
    reasoning was right and was applied one field away from where it was most
    needed.

    Deciding what several disagreeing statements jointly assert is a
    judgement, not an arithmetic fold, and re-projection is not entitled to
    make it: #137 put contested values in the edge table so disagreement
    survives as data, and #140 owns who resolves it. Until then the existing
    prose is preserved and the constraint is reported as contested.
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
    #
    # day_types now has a writer (the tier judgement), so take it from the
    # newest observation that names any; carry the existing constraint's value
    # only when no judgement does. Before the writer existed this was carried
    # unconditionally, because rebuilding from a judgement that could not
    # express it silently unscoped 22 of 33 constraints on the real store.
    carried_day_types = list(existing.applicability.day_types) if existing else []
    judged_day_types = next(
        (list(judgements[o.uid].tier.day_types) for o in reversed(ordered)
         if judgements[o.uid].tier.day_types),
        carried_day_types,
    )

    applicability = Applicability(day_types=judged_day_types)
    for observation in reversed(ordered):
        judgement = judgements[observation.uid].tier
        if judgement.start_date or judgement.end_date or judgement.days_of_week:
            applicability = Applicability(
                start_date=judgement.start_date,
                end_date=judgement.end_date,
                days_of_week=judgement.days_of_week,
                day_types=judged_day_types,
            )
            break

    derived = {
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
        # Required once, required after: the newest observation that names a
        # kind wins, and one that names none does not unset it -- so the
        # fallback is the value the constraint already carries, not None. The
        # same fold projection's branch follows, and for the same reason: a
        # judgement answering null does not mean "this rule stopped requiring
        # a block", it means this pass had nothing to say. Rebuilding from it
        # would unset the requirement silently, and the only symptom is a block
        # nobody places and nobody nags about.
        #
        # Durable-only (spec decision 10): a session-tier statement about
        # tomorrow's session is a fact for the planner, not a standing
        # requirement, and projection's session branch never writes the field.
        # Gating on the *derived* tier rather than the existing one keeps the
        # two halves of this function agreeing about one constraint.
        "requires_block": (
            next(
                (judgements[o.uid].requires_block.slug for o in reversed(ordered)
                 if judgements[o.uid].requires_block.slug is not None),
                existing.requires_block if existing else None,
            )
            if tier is Tier.DURABLE
            else None
        ),
    }
    if len(ordered) == 1:
        # One observation cannot contradict itself, so there is nothing to
        # decide and the label improvement should reach it.
        derived["name"] = newest_j.label or newest.text
        derived["description"] = newest.text
    return derived


async def reproject(
    observation_store: ObservationStore,
    constraint_store: ConstraintStore,
    judge: Judge,
    *,
    kinds: list[str] = (),
    uid: str | None = None,
    apply: bool = False,
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

    Previews by default and writes nothing unless `apply=True`. Re-projection
    rewrites derived state across the whole store, and until now it reported
    what it changed as advisory data with nothing gating on it — so every safe
    run was safe because a person read the report and would have stopped. That
    is not a property of the code. It destroyed the description of 8 of 12
    multi-observation constraints on a copy of the real corpus, and nothing
    here would have refused.

    A caller that wants the change asks for it twice: once to see it, once to
    mean it.

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
    report = ReprojectionReport(examined=len(targets), applied=apply)

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

        judgements = await _judge_all(observations, judge, kinds)
        derived = _derive(observations, judgements, constraint)
        if len(observations) > 1:
            report.contested.append(
                ContestedConstraint(
                    uid=constraint.uid,
                    name=constraint.name,
                    observations=len(observations),
                )
            )

        changed_fields = [
            field
            for field, value in derived.items()
            if getattr(constraint, field) != value
        ]
        if not changed_fields:
            report.unchanged += 1
            continue

        if not apply:
            report.changed.append(
                ConstraintChange(
                    uid=constraint.uid, name=constraint.name, fields=changed_fields
                )
            )
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


async def split(
    observation_store: ObservationStore,
    constraint_store: ConstraintStore,
    judge: Judge,
    *,
    uid: str,
    observation_uids: list[str],
    anchor_store: "AnchorStore | None" = None,
    kinds: list[str] = (),
) -> tuple[str, str]:
    """Separate observations wrongly folded into one constraint.

    The store could merge and could not un-merge. A wrong merge was permanent
    in L2 while being fully recoverable from L1 — I2 doing exactly its job and
    L2 having no counterpart to it. Undoing the one that reached the real
    corpus required deleting a row from `constraint_observations` by hand,
    which is the corrective operation the store most needed being performed
    underneath the store.

    Mechanical on purpose: the caller names which observations leave, and
    nothing here judges whether they should. Deciding that two statements are
    not the same rule is the same judgement `canonicalise` makes, and asking a
    model to revise its own merge unattended is a different and worse thing
    than asking it prospectively.

    Available without ceremony, deliberately. Merges happen unattended in the
    write path, so gating the repair while leaving the damage ungated makes
    corruption cheap and correction expensive — a ratchet in the wrong
    direction. Whoever may write may repair.

    Identity: the original keeps its uid (I3), the remainder is a newly minted
    constraint. Both are re-projected from the observations they end up with,
    so their derived fields describe what each actually holds.

    Returns (original_uid, new_uid).
    """
    constraint = constraint_store.get(uid)
    if constraint is None:
        raise ValueError(f"cannot split unknown constraint {uid!r}")

    present = set(constraint_store.observations_for(uid))
    moving = list(dict.fromkeys(observation_uids))
    if not moving:
        raise ValueError(
            f"refusing to split constraint {uid!r} with no observations named; "
            f"a split that moves nothing would mint an empty constraint"
        )
    unknown = [o for o in moving if o not in present]
    if unknown:
        raise ValueError(
            f"observation(s) {sorted(unknown)} are not provenance of constraint "
            f"{uid!r}; splitting would attach evidence it never had"
        )
    remaining = [o for o in present if o not in set(moving)]
    if not remaining:
        raise ValueError(
            f"refusing to move every observation off constraint {uid!r}; that "
            f"is a rename, and it would leave a constraint with no evidence "
            f"that re-projection could never re-derive"
        )

    # Mint rather than reuse (I3). Fields are placeholders: both constraints
    # are re-projected below, from the evidence each ends up holding.
    newborn = Constraint(
        name=constraint.name,
        description=constraint.description,
        necessity=constraint.necessity,
        scope=constraint.scope,
        status=constraint.status,
        source=constraint.source,
        tier=constraint.tier,
        applicability=constraint.applicability,
        source_observation_uids=moving,
        created_at=constraint.created_at,
        decay_class=constraint.decay_class,
        last_observed_at=constraint.last_observed_at,
    )
    constraint_store.upsert(newborn)
    constraint_store.replace_links(uid, remaining)

    for target in (uid, newborn.uid):
        # apply=True: the caller already committed to the split by calling
        # this, and leaving the halves un-derived would be worse than either
        # outcome — each would keep describing the merge it came from.
        await reproject(
            observation_store,
            constraint_store,
            judge,
            kinds=kinds,
            uid=target,
            apply=True,
        )

    if anchor_store is not None:
        # A split constraint with no anchors is unreachable from any walk,
        # which is indistinguishable from a rule that does not apply today.
        for target, uids in ((uid, remaining), (newborn.uid, moving)):
            names: list[str] = []
            for observation_uid in uids:
                observation = observation_store.get(observation_uid)
                if observation is not None:
                    names.extend(observation.anchors)
            resolved = await resolve_anchors(
                list(dict.fromkeys(names)), anchor_store, judge
            )
            anchor_store.replace_constraint_links(target, resolved)

    return uid, newborn.uid
