# src/memory/projection.py
from __future__ import annotations

from memory.constraint import Applicability, Constraint
from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import Judge
from memory.models import Observation, Tier


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
    if not ingest_result.stored:
        raise ValueError(
            f"refusing to project an observation that was not stored "
            f"(suppressed_as={ingest_result.suppressed_as!r}); its provenance "
            f"would dangle and re-projection could never reproduce it"
        )

    # Session-tier observations are not canonicalised. The spec is explicit:
    # the session tier is fast, total-recall and mortal, with no
    # canonicalisation. Skipping it here does three things at once — it keeps
    # a session restatement from demoting a durable rule to SESSION and out of
    # the read path, it bounds the candidate list to durable constraints so the
    # write-path prompt cannot grow without limit, and it saves a model call.
    if ingest_result.tier is not Tier.DURABLE:
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

    candidates = constraint_store.durable()
    judgement = await judge.canonicalise(observation, candidates)

    if judgement.constraint_uid is not None:
        # The id came from the model. Verify it names a constraint we actually
        # minted before folding user data into it — set membership over
        # system-minted uids, explicitly outside the no-matching rule. Note we
        # verify against the snapshot shown to the model, so a uid minted
        # concurrently after that snapshot raises rather than being acted on.
        known = {c.uid: c for c in candidates}
        if judgement.constraint_uid not in known:
            raise ValueError(
                f"judge returned unknown constraint_uid "
                f"{judgement.constraint_uid!r}; not among {len(known)} candidates"
            )
        existing = known[judgement.constraint_uid]
        constraint_store.link_observation(existing.uid, observation.uid)
        # Tier only ever moves up. A durable rule is never demoted by a
        # later observation; that would be last-write-wins, which this
        # design rejects explicitly.
        return constraint_store.get(existing.uid)

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
