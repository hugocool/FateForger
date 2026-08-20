# src/memory/ingest.py
from __future__ import annotations

import asyncio
from datetime import date

from pydantic import BaseModel, Field

from memory.judge import Judge
from memory.models import DecayClass, Observation, Provenance, Tier
from memory.store import ObservationStore


class IngestResult(BaseModel):
    stored: bool
    uid: str | None = None
    tier: Tier = Tier.SESSION
    anchors: list[str] = Field(default_factory=list)
    label: str = ""
    is_declaration: bool = False
    # Whether breaking the rule ruins the day. Its own judgement rather than a
    # field on the tier call: the tier prompt already answers four things, and
    # a prompt that names a category without giving the model something to key
    # off produces a near coin flip (see CLAUDE.md). Concurrent, so a fifth
    # question costs no latency.
    is_binding: bool = False
    suppressed_as: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    days_of_week: list[int] = Field(default_factory=list)  # 0=Mon .. 6=Sun
    # Default PERMANENT: a rule wrongly marked permanent is merely noisy, one
    # wrongly marked short-lived disappears without being asked.
    decay_class: DecayClass = DecayClass.PERMANENT


async def ingest(
    observation: Observation, judge: Judge, store: ObservationStore
) -> IngestResult:
    """Judge an observation and append it unless it should be suppressed.

    The five judgements are independent, so they are issued concurrently:
    one round-trip of latency rather than five. Nothing here inspects the
    observation's text — every decision about meaning comes from the judge.
    """
    if observation.provenance is not Provenance.OBSERVED:
        # A rule's own output must never re-enter as evidence, and rejecting
        # it costs no LLM call.
        return IngestResult(stored=False, suppressed_as="generated")

    recent = (
        store.by_session(observation.session_id) if observation.session_id else []
    )
    # return_exceptions=True so a failing judgement cannot orphan its four
    # siblings: with the default, gather propagates the first exception but
    # leaves the others running, discarding their results and errors. We
    # await all five, then re-raise the first failure to preserve the
    # ValueError contract callers rely on.
    results = await asyncio.gather(
        judge.anchors(observation),
        judge.tier(observation),
        judge.meta(observation),
        judge.dedup(observation, recent),
        judge.necessity(observation),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    anchor_j, tier_j, meta_j, dedup_j, necessity_j = results

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
    store.append(observation)
    return IngestResult(
        stored=True,
        uid=observation.uid,
        tier=tier_j.tier,
        anchors=anchor_j.anchors,
        label=tier_j.label,
        is_declaration=tier_j.is_declaration,
        is_binding=necessity_j.is_binding,
        start_date=tier_j.start_date,
        end_date=tier_j.end_date,
        days_of_week=tier_j.days_of_week,
        decay_class=tier_j.decay_class,
    )
