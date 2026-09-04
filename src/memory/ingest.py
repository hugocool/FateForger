# src/memory/ingest.py
from __future__ import annotations

import asyncio
import weakref
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
    day_types: list[str] = Field(default_factory=list)
    # Default PERMANENT: a rule wrongly marked permanent is merely noisy, one
    # wrongly marked short-lived disappears without being asked.
    decay_class: DecayClass = DecayClass.PERMANENT
    # The registered kind this rule says must be on the day; see projection
    # for the durable-only rule.
    requires_block: str | None = None


# One lock per (store, session), weak-keyed so a collected store does not leak
# its locks. Same shape and same reason as projection's and anchoring's: the
# span below reads the session's observations, asks a model whether this
# restates one of them, and appends — so two statements arriving concurrently
# on one session each see a candidate list without the other, and dedup misses
# a restatement it would have caught sequentially.
#
# Measured: two concurrent identical observes produced two observations where
# two sequential ones produced one. L1 is append-only, so that duplicate is
# permanent, and evidence is what promotion and decay count — the same harm as
# #168, arriving by concurrency rather than by retry. write_uid cannot help
# here, because two genuinely concurrent statements carry different write ids.
#
# Keyed per session rather than per store on purpose: a host running several
# conversations must not serialise them against each other.
_SESSION_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _lock_for(store: ObservationStore, session_id: str) -> asyncio.Lock:
    locks = _SESSION_LOCKS.setdefault(store, {})
    lock = locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[session_id] = lock
    return lock


async def ingest(
    observation: Observation,
    judge: Judge,
    store: ObservationStore,
    *,
    kinds: list[str] = (),
) -> IngestResult:
    """Judge an observation and append it unless it should be suppressed.

    The six judgements are independent, so they are issued concurrently:
    one round-trip of latency rather than six. Nothing here inspects the
    observation's text — every decision about meaning comes from the judge.
    """
    if observation.provenance is not Provenance.OBSERVED:
        # A rule's own output must never re-enter as evidence, and rejecting
        # it costs no LLM call.
        return IngestResult(stored=False, suppressed_as="generated")

    if observation.session_id is None:
        # Nothing to be stale against: dedup only ever compares within a
        # session, so an unscoped observation needs no lock and takes none.
        return await _ingest(observation, judge, store, recent=[], kinds=kinds)

    async with _lock_for(store, observation.session_id):
        return await _ingest(
            observation,
            judge,
            store,
            recent=store.by_session(observation.session_id),
            kinds=kinds,
        )


async def _ingest(
    observation: Observation,
    judge: Judge,
    store: ObservationStore,
    *,
    recent: list[Observation],
    kinds: list[str],
) -> IngestResult:
    """The judged span. Callers hold the session lock around this."""
    # return_exceptions=True so a failing judgement cannot orphan its five
    # siblings: with the default, gather propagates the first exception but
    # leaves the others running, discarding their results and errors. We
    # await all six, then re-raise the first failure to preserve the
    # ValueError contract callers rely on.
    results = await asyncio.gather(
        judge.anchors(observation),
        judge.tier(observation),
        judge.meta(observation),
        judge.dedup(observation, recent),
        judge.necessity(observation),
        judge.requires_block(observation, list(kinds)),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    anchor_j, tier_j, meta_j, dedup_j, necessity_j, requires_j = results

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
        is_binding=necessity_j.is_binding,
        start_date=tier_j.start_date,
        end_date=tier_j.end_date,
        days_of_week=tier_j.days_of_week,
        day_types=tier_j.day_types,
        decay_class=tier_j.decay_class,
        requires_block=requires_j.slug,
    )
