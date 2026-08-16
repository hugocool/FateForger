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


def await_sync(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)
