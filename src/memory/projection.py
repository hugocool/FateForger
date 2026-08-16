# src/memory/projection.py
from __future__ import annotations

from memory.constraint import Applicability, Constraint
from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import Judge
from memory.models import Observation


async def project(
    observation: Observation,
    ingest_result: IngestResult,
    judge: Judge,
    constraint_store: ConstraintStore,
) -> Constraint:
    """Turn a stored observation into, or fold it into, a constraint.

    L2 is derived from L1: the constraint records which observations produced
    it, so re-projection is possible when the taxonomy changes (I4).
    """
    candidates = constraint_store.all()
    judgement = await judge.canonicalise(observation, candidates)

    if judgement.constraint_uid is not None:
        # The id came from the model. Verify it names a constraint we actually
        # minted before folding user data into it — set membership over
        # system-minted uids, explicitly outside the no-matching rule.
        known = {c.uid: c for c in candidates}
        if judgement.constraint_uid not in known:
            raise ValueError(
                f"judge returned unknown constraint_uid "
                f"{judgement.constraint_uid!r}; not among {len(known)} candidates"
            )
        existing = known[judgement.constraint_uid]
        if observation.uid not in existing.source_observation_uids:
            existing.source_observation_uids.append(observation.uid)
        existing.tier = ingest_result.tier
        constraint_store.upsert(existing)
        return existing

    created = Constraint(
        name=observation.text,
        description=observation.text,
        necessity="should",
        scope="profile",
        status="proposed",
        source=observation.channel.value,
        tier=ingest_result.tier,
        applicability=Applicability(),
        source_observation_uids=[observation.uid],
        created_at=observation.observed_at,
    )
    constraint_store.upsert(created)
    return created
