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
    anchor_j, tier_j, meta_j, dedup_j = await asyncio.gather(
        judge.anchors(observation),
        judge.tier(observation),
        judge.meta(observation),
        judge.dedup(observation, recent),
    )

    if meta_j.is_meta:
        return IngestResult(stored=False, suppressed_as="meta")
    if dedup_j.duplicate_of is not None:
        return IngestResult(stored=False, suppressed_as="duplicate")

    observation.anchors = anchor_j.anchors
    uid = store.append(observation)
    return IngestResult(
        stored=True, uid=uid, tier=tier_j.tier, anchors=anchor_j.anchors
    )
