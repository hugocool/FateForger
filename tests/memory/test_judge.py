# tests/memory/test_judge.py
from __future__ import annotations

from datetime import datetime, timezone

from memory.judge import (
    AnchorJudgement,
    Judge,
    StubJudge,
    TierJudgement,
)
from memory.models import Channel, Observation, Provenance, Tier

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def test_stub_returns_its_canned_anchor_answer():
    judge = StubJudge(anchors={"eat oats before gym": ["oats", "gym"]})
    result = await_sync(judge.anchors(_obs("eat oats before gym")))
    assert isinstance(result, AnchorJudgement)
    assert result.anchors == ["oats", "gym"]


def test_stub_returns_its_canned_tier_answer():
    judge = StubJudge(tiers={"eat oats before gym": Tier.DURABLE})
    result = await_sync(judge.tier(_obs("eat oats before gym")))
    assert isinstance(result, TierJudgement)
    assert result.tier is Tier.DURABLE


def test_stub_defaults_are_conservative():
    """An unstubbed question must not silently promote or suppress."""
    judge = StubJudge()
    assert await_sync(judge.anchors(_obs("anything"))).anchors == []
    assert await_sync(judge.tier(_obs("anything"))).tier is Tier.SESSION
    assert await_sync(judge.meta(_obs("anything"))).is_meta is False
    assert await_sync(judge.dedup(_obs("anything"), [])).duplicate_of is None


def test_stub_records_what_it_was_asked():
    """Ingest tests need to assert which questions were put to the model."""
    judge = StubJudge()
    obs = _obs("eat oats before gym")
    await_sync(judge.anchors(obs))
    await_sync(judge.meta(obs))
    assert judge.calls == [("anchors", obs.uid), ("meta", obs.uid)]


def test_stub_satisfies_the_protocol():
    assert isinstance(StubJudge(), Judge)


def test_stub_returns_its_canned_required_kind():
    from memory.judge import RequiresBlockJudgement

    judge = StubJudge(requires_blocks={"a planning session every working day": "planning"})
    result = await_sync(
        judge.requires_block(_obs("a planning session every working day"), ["planning", "sleep"])
    )
    assert result == RequiresBlockJudgement(slug="planning")
    assert await_sync(judge.requires_block(_obs("sleep at 23:00"), ["planning"])).slug is None
    assert ("requires_block", judge.calls[-1][1]) == judge.calls[-1]


def await_sync(coro):
    """Run one coroutine from a synchronous test, owning the loop.

    `asyncio.get_event_loop()` was used here, and it only worked by accident.
    On Python 3.11 it returns the RUNNING loop if there is one, otherwise the
    loop someone previously set, and raises when neither exists. These are
    sync tests, so there is never a running loop -- they depended entirely on
    some earlier test having left one set.

    `pytest-asyncio` in auto mode creates a loop per async test and closes it,
    so whether one is left behind depends on what ran before. `pytest
    tests/memory` alone passed; `pytest tests/unit tests/memory` failed six
    tests with `RuntimeError: There is no current event loop in thread
    'MainThread'` (#269). The suite's result depended on the order the
    directories were named in, in the one area that holds Hugo's real
    preference corpus.

    `asyncio.run` creates a fresh loop, runs the coroutine, and closes it. It
    borrows nothing, so nothing can take it away.
    """
    import asyncio

    return asyncio.run(coro)
