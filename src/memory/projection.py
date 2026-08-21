# src/memory/projection.py
from __future__ import annotations

import asyncio
import weakref

from memory.constraint import (
    Applicability,
    Constraint,
    Necessity,
    Scope,
    Source,
    Status,
)
from memory.anchor_store import AnchorStore
from memory.anchoring import resolve_anchors
from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import Judge
from memory.models import Channel, Observation, Tier

# Channel is where a statement arrived; source is who asserted it. A rule
# given in weekly review and one given mid-planning are both the user's.
_SOURCE_BY_CHANNEL = {
    Channel.PLANNING: Source.USER,
    Channel.REVIEW: Source.USER,
    Channel.CALENDAR: Source.CALENDAR,
}

# One lock per constraint store, weak-keyed so a collected store does not leak
# its lock. The read-judge-write span in project() includes a model round-trip,
# so two concurrent projections of the same rule can each see a candidate list
# lacking the other's constraint, each be told "this is new", and each create a
# row — duplication in the layer whose whole job is canonicalisation.
_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _lock_for(constraint_store: ConstraintStore) -> asyncio.Lock:
    lock = _LOCKS.get(constraint_store)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[constraint_store] = lock
    return lock


async def project(
    observation: Observation,
    ingest_result: IngestResult,
    judge: Judge,
    constraint_store: ConstraintStore,
    anchor_store: AnchorStore | None = None,
) -> Constraint:
    """Turn a stored observation into, or fold it into, a constraint.

    L2 is derived from L1: the constraint records which observations produced
    it, so re-projection is possible when the taxonomy changes (I4).
    """
    if not ingest_result.stored:
        raise ValueError(
            f"refusing to project an observation that was not stored "
            f"(suppressed_as={ingest_result.suppressed_as!r}); its provenance "
            f"would dangle and re-projection could never reproduce it"
        )

    # Everything below spans a read of candidates, a model round-trip, and a
    # write; see _LOCKS above for why that span must be serialised per store.
    async with _lock_for(constraint_store):
        # Session-tier observations are not canonicalised. The spec is
        # explicit: the session tier is fast, total-recall and mortal, with
        # no canonicalisation. Skipping it here does three things at once —
        # it keeps a session restatement from demoting a durable rule to
        # SESSION and out of the read path, it bounds the candidate list to
        # durable constraints so the write-path prompt cannot grow without
        # limit, and it saves a model call.
        if ingest_result.tier is not Tier.DURABLE:
            created = Constraint(
                name=ingest_result.label or observation.text,
                description=observation.text,
                necessity=Necessity.MUST
                if ingest_result.is_binding
                else Necessity.SHOULD,
                scope=Scope.SESSION,
                status=Status.PROPOSED,
                source=_SOURCE_BY_CHANNEL[observation.channel],
                tier=ingest_result.tier,
                applicability=Applicability(
                    start_date=ingest_result.start_date,
                    end_date=ingest_result.end_date,
                    days_of_week=ingest_result.days_of_week,
                ),
                source_observation_uids=[observation.uid],
                created_at=observation.observed_at,
                decay_class=ingest_result.decay_class,
                last_observed_at=observation.observed_at,
            )
            constraint_store.upsert(created)
            await _attach_anchors(created.uid, ingest_result, anchor_store, judge)
            return created

        candidates = constraint_store.durable()
        judgement = await judge.canonicalise(observation, candidates)

        if judgement.constraint_uid is not None:
            # The id came from the model. Verify it names a constraint we
            # actually minted before folding user data into it — set
            # membership over system-minted uids, explicitly outside the no-
            # matching rule. Note we verify against the snapshot shown to the
            # model, so a uid minted concurrently after that snapshot raises
            # rather than being acted on.
            known = {c.uid: c for c in candidates}
            if judgement.constraint_uid not in known:
                raise ValueError(
                    f"judge returned unknown constraint_uid "
                    f"{judgement.constraint_uid!r}; not among {len(known)} candidates"
                )
            existing = known[judgement.constraint_uid]
            constraint_store.link_observation(existing.uid, observation.uid)
            # Re-read: `existing` predates link_observation, so its
            # source_observation_uids is stale. upsert() replaces provenance
            # links with whatever list it is given (replace_links), so
            # upserting the stale object here would silently drop the link
            # just added above.
            existing = constraint_store.get(existing.uid)
            # Newest evidence wins. Backfill replays historical rows, so a
            # fold can carry an OLDER observation than the constraint already
            # has.
            if observation.observed_at > existing.last_observed_at:
                existing.last_observed_at = observation.observed_at
            constraint_store.upsert(existing)
            # A fold adds evidence, so it can add anchors the constraint was
            # not previously reachable from. Links are replaced rather than
            # appended, so the union is assembled here.
            await _attach_anchors(
                existing.uid, ingest_result, anchor_store, judge, extend=True
            )
            # Tier only ever moves up. A durable rule is never demoted by a
            # later observation; that would be last-write-wins, which this
            # design rejects explicitly.
            return constraint_store.get(existing.uid)

        created = Constraint(
            name=ingest_result.label or observation.text,
            description=observation.text,
            necessity=Necessity.MUST
            if ingest_result.is_binding
            else Necessity.SHOULD,
            scope=Scope.PROFILE if ingest_result.tier is Tier.DURABLE else Scope.SESSION,
            status=Status.PROPOSED,
            source=_SOURCE_BY_CHANNEL[observation.channel],
            tier=ingest_result.tier,
            applicability=Applicability(
                start_date=ingest_result.start_date,
                end_date=ingest_result.end_date,
                days_of_week=ingest_result.days_of_week,
            ),
            source_observation_uids=[observation.uid],
            created_at=observation.observed_at,
            decay_class=ingest_result.decay_class,
            last_observed_at=observation.observed_at,
        )
        constraint_store.upsert(created)
        await _attach_anchors(created.uid, ingest_result, anchor_store, judge)
        return created


async def _attach_anchors(
    constraint_uid: str,
    ingest_result: IngestResult,
    anchor_store: AnchorStore | None,
    judge: Judge,
    *,
    extend: bool = False,
) -> None:
    """Link a constraint to the anchors its observation mentioned.

    Optional store because the graph is additive: a host that has not built
    one still gets the constraint layer it had before, and nothing here can
    fail a write that would otherwise have succeeded.
    """
    if anchor_store is None or not ingest_result.anchors:
        return
    uids = await resolve_anchors(ingest_result.anchors, anchor_store, judge)
    if extend:
        uids = sorted(set(uids) | set(anchor_store.anchors_for(constraint_uid)))
    anchor_store.replace_constraint_links(constraint_uid, uids)
