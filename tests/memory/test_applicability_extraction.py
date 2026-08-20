# tests/memory/test_applicability_extraction.py
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import StubJudge, TierJudgement
from memory.models import Channel, Observation, Provenance, Tier
from memory.projection import project
from memory.read_api import get_active_constraints

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
MONDAY = date(2026, 8, 17)
TUESDAY = date(2026, 8, 18)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def test_tier_judgement_defaults_to_unconstrained():
    """A rule with no stated scoping must apply every day, never no day."""
    j = TierJudgement(label="x")
    assert j.start_date is None
    assert j.end_date is None
    assert j.days_of_week == []


def test_stub_returns_canned_days_of_week():
    judge = StubJudge(days_of_week={"client on Tue and Thu": [1, 3]})
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        judge.tier(_obs("client on Tue and Thu"))
    )
    assert result.days_of_week == [1, 3]


async def test_days_of_week_reaches_the_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("go to client on Tuesdays and Thursdays")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Client attendance days",
        days_of_week=[1, 3],
    )
    c = await project(obs, result, StubJudge(), store)
    assert c.applicability.days_of_week == [1, 3]


async def test_a_tuesday_thursday_rule_is_not_served_on_monday(tmp_path):
    """The measured defect: this rule was returned for a Monday."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("go to client on Tuesdays and Thursdays")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Client attendance days",
        days_of_week=[1, 3],
    )
    await project(obs, result, StubJudge(), store)
    assert get_active_constraints(store, MONDAY) == []
    assert len(get_active_constraints(store, TUESDAY)) == 1


async def test_a_date_range_reaches_the_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("this sprint, cap framing at 15 minutes")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Framing cap",
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 14),
    )
    c = await project(obs, result, StubJudge(), store)
    assert c.applicability.start_date == date(2026, 8, 1)
    assert c.applicability.end_date == date(2026, 8, 14)
    assert get_active_constraints(store, MONDAY) == []


async def test_an_unscoped_rule_still_applies_every_day(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("sleep at 23:00")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE, label="Sleep schedule",
    )
    await project(obs, result, StubJudge(), store)
    assert len(get_active_constraints(store, MONDAY)) == 1
    assert len(get_active_constraints(store, TUESDAY)) == 1
