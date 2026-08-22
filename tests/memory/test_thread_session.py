# tests/memory/test_thread_session.py
"""The thread-remembers-things binding, with the judge stubbed."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from memory.judge import StubJudge
from memory.models import Channel, Tier
from memory.service import MemoryService
from memory.thread_session import ForeignSpeaker, ThreadKnowledge, ThreadSession

THREAD = "C0AA6HC1RJL:1772000000.001"
OTHER = "C0AA6HC1RJL:1772000000.999"
NOW = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)


def _session(tmp_path, **judge_kwargs) -> ThreadSession:
    judge = StubJudge(tiers=judge_kwargs.pop("tiers", None), **judge_kwargs)
    return ThreadSession(MemoryService(str(tmp_path / "m.db"), judge))


async def test_a_first_turn_recalls_nothing_rather_than_failing(tmp_path):
    """The empty case is normal, not exceptional — it is every thread's start."""
    knowledge = _session(tmp_path).recall(THREAD)
    assert knowledge.established == []
    assert knowledge.is_first_turn is True
    assert knowledge.added is None


async def test_what_one_turn_says_the_next_turn_recalls(tmp_path):
    """FM1: the user should not have to restate themselves between turns."""
    session = _session(tmp_path, tiers={"gym at 19:00 tomorrow": Tier.SESSION})

    first = await session.observe(THREAD, "gym at 19:00 tomorrow", observed_at=NOW)
    assert first.added is not None
    assert first.is_first_turn is True

    later = session.recall(THREAD)
    assert [c.uid for c in later.established] == [first.added.uid]
    assert later.is_first_turn is False


async def test_one_thread_cannot_read_another_threads_session(tmp_path):
    """Sessions are scoped by id, so two conversations never bleed."""
    session = _session(tmp_path, tiers={"gym at 19:00": Tier.SESSION})
    await session.observe(THREAD, "gym at 19:00", observed_at=NOW)
    assert session.recall(OTHER).established == []


async def test_a_suppressed_statement_is_reported_not_silently_dropped(tmp_path):
    """"Nothing worth keeping" and "the write failed" must not look alike."""
    judge = StubJudge(metas={"thanks!": True})
    session = ThreadSession(MemoryService(str(tmp_path / "m.db"), judge))

    knowledge = await session.observe(THREAD, "thanks!", observed_at=NOW)
    assert knowledge.added is None
    assert knowledge.suppressed_as == "meta"


async def test_a_judge_failure_propagates_rather_than_reading_as_empty(tmp_path):
    """A misconfigured host must not be indistinguishable from a quiet user."""

    class Broken(StubJudge):
        async def tier(self, observation):
            raise RuntimeError("sampling unavailable")

    session = ThreadSession(MemoryService(str(tmp_path / "m.db"), Broken()))
    with pytest.raises(RuntimeError, match="sampling unavailable"):
        await session.observe(THREAD, "gym at 19:00", observed_at=NOW)


async def test_a_foreign_speaker_is_refused_loudly(tmp_path):
    """The store is single-tenant: an Observation carries no user.

    Recording a colleague's aside would file their preference as the owner's,
    with nothing marking it and nothing later able to find it.
    """
    judge = StubJudge(tiers={"I like 6am starts": Tier.SESSION})
    session = ThreadSession(
        MemoryService(str(tmp_path / "m.db"), judge), owner_user_id="U_HUGO"
    )

    with pytest.raises(ForeignSpeaker, match="single-tenant"):
        await session.observe(
            THREAD, "I like 6am starts", user_id="U_COLLEAGUE", observed_at=NOW
        )

    assert session.recall(THREAD).established == []


async def test_the_owner_is_recorded_normally(tmp_path):
    judge = StubJudge(tiers={"gym at 19:00": Tier.SESSION})
    session = ThreadSession(
        MemoryService(str(tmp_path / "m.db"), judge), owner_user_id="U_HUGO"
    )
    knowledge = await session.observe(
        THREAD, "gym at 19:00", user_id="U_HUGO", observed_at=NOW
    )
    assert knowledge.added is not None


async def test_recall_without_a_day_applies_no_expiry(tmp_path):
    """Reconstructing a transcript wants everything ever established."""
    judge = StubJudge(
        tiers={"gym at 19:00": Tier.SESSION},
        decay_classes=None,
    )
    session = ThreadSession(MemoryService(str(tmp_path / "m.db"), judge))
    long_ago = NOW - timedelta(days=400)
    await session.observe(THREAD, "gym at 19:00", observed_at=long_ago)

    assert len(session.recall(THREAD).established) == 1


def test_thread_knowledge_is_immutable():
    """It is a report of a moment, not a handle to mutate."""
    knowledge = ThreadKnowledge(session_id=THREAD)
    with pytest.raises(Exception):
        knowledge.session_id = "other"  # type: ignore[misc]
