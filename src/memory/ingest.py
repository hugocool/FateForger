# src/memory/ingest.py
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from memory.judge import Judge
from memory.models import Observation, Provenance, Tier
from memory.store import ObservationStore


class IngestResult(BaseModel):
    stored: bool
    uid: str | None = None
    tier: Tier = Tier.SESSION
    anchors: list[str] = Field(default_factory=list)
    suppressed_as: str | None = None


async def ingest(
    observation: Observation, judge: Judge, store: ObservationStore
) -> IngestResult:
    """Judge an observation and append it unless it should be suppressed.

    The four judgements are independent, so they are issued concurrently:
    one round-trip of latency rather than four. Nothing here inspects the
    observation's text — every decision about meaning comes from the judge.
    """
    if observation.provenance is not Provenance.OBSERVED:
        # A rule's own output must never re-enter as evidence, and rejecting
        # it costs no LLM call.
        return IngestResult(stored=False, suppressed_as="generated")

    recent = (
        store.by_session(observation.session_id) if observation.session_id else []
    )
    # return_exceptions=True so a failing judgement cannot orphan its three
    # siblings: with the default, gather propagates the first exception but
    # leaves the others running, discarding their results and errors. We
    # await all four, then re-raise the first failure to preserve the
    # ValueError contract callers rely on.
    results = await asyncio.gather(
        judge.anchors(observation),
        judge.tier(observation),
        judge.meta(observation),
        judge.dedup(observation, recent),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    anchor_j, tier_j, meta_j, dedup_j = results

    if meta_j.is_meta:
        return IngestResult(stored=False, suppressed_as="meta")
    if dedup_j.duplicate_of is not None:
        # The id came from the model. Verify it names an observation we
        # actually minted before discarding user data on its say-so: this
        # store is append-only, so a suppression caused by a hallucinated
        # id is permanent and invisible. Set membership over system-minted
        # uids is explicitly outside the no-matching rule.
        known = {o.uid for o in recent}
        if dedup_j.duplicate_of not in known:
            raise ValueError(
                f"judge returned unknown duplicate_of {dedup_j.duplicate_of!r}; "
                f"not among {len(known)} candidate uids"
            )
        return IngestResult(stored=False, suppressed_as="duplicate")

    observation.anchors = anchor_j.anchors
    uid = store.append(observation)
    return IngestResult(
        stored=True, uid=uid, tier=tier_j.tier, anchors=anchor_j.anchors
    )
