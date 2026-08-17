# tests/memory/test_eval_extraction.py
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from memory.models import Channel, DecayClass, Observation, Provenance, Tier
from memory.openrouter_judge import OpenRouterJudge

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
    )


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="eval",
        observed_at=T0,
    )


async def test_finds_gym_which_pattern_matching_scored_at_zero():
    """The measured failure of the discarded implementation."""
    async with _judge() as judge:
        result = await judge.anchors(_obs("Eat oats 2 hours before going to the gym"))
    assert "gym" in [a.lower() for a in result.anchors]


async def test_a_real_preference_mentioning_a_session_is_not_meta():
    """The marker list would have suppressed this permanently."""
    async with _judge() as judge:
        result = await judge.meta(_obs("Gym Session — user goes to the gym at 18:00"))
    assert result.is_meta is False


async def test_interaction_chatter_is_meta():
    async with _judge() as judge:
        result = await judge.meta(
            _obs("The user wants to begin the timeboxing session immediately")
        )
    assert result.is_meta is True


async def test_a_schedule_structure_rule_is_not_meta():
    """The first corpus run suppressed 32 real rules as meta; this pins the fix."""
    async with _judge() as judge:
        result = await judge.meta(
            _obs("Deep Work Block Duration: Deep Work (DW) blocks are usually 2 hours long")
        )
    assert result.is_meta is False


async def test_a_daily_meal_rule_is_not_meta():
    async with _judge() as judge:
        result = await judge.meta(
            _obs("Daily Meals: Include breakfast, lunch, and dinner every day.")
        )
    assert result.is_meta is False


async def test_a_block_inclusion_rule_is_not_meta():
    async with _judge() as judge:
        result = await judge.meta(
            _obs("Always include planning session: Always include a planning session in the schedule.")
        )
    assert result.is_meta is False


async def test_methodology_preference_is_still_meta():
    """The fix must not swing the other way: tool talk stays suppressed."""
    async with _judge() as judge:
        result = await judge.meta(
            _obs("Timeboxing Preference: Apply timeboxing methodology to the scheduling process.")
        )
    assert result.is_meta is True


async def test_a_standing_rule_is_durable_and_a_declaration():
    async with _judge() as judge:
        result = await judge.tier(_obs("I never schedule meetings before 13:00"))
    assert result.tier is Tier.DURABLE
    assert result.is_declaration is True
    assert result.label, "the model must name the rule, not leave it unnamed"


async def test_todays_appointment_is_session_scoped():
    async with _judge() as judge:
        result = await judge.tier(_obs("Hockey game today at 11:45 at VVV"))
    assert result.tier is Tier.SESSION


def _constraint(name: str, description: str):
    from datetime import datetime, timezone

    from memory.constraint import (
        Applicability,
        Constraint,
        Necessity,
        Scope,
        Source,
        Status,
    )
    from memory.models import Tier

    return Constraint(
        name=name,
        description=description,
        necessity=Necessity.MUST,
        scope=Scope.PROFILE,
        status=Status.LOCKED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[],
        created_at=datetime(2026, 3, 9, tzinfo=timezone.utc),
        last_observed_at=datetime(2026, 3, 9, tzinfo=timezone.utc),
    )


async def test_a_reworded_restatement_is_recognised_as_the_same_rule():
    existing = _constraint("Oats before gym", "Eat oats two hours before the gym")
    async with _judge() as judge:
        result = await judge.canonicalise(
            _obs("I need my oats a couple of hours ahead of training"), [existing]
        )
    assert result.constraint_uid == existing.uid


async def test_a_different_rule_on_the_same_topic_is_not_merged():
    """The failure that would silently destroy a real preference."""
    existing = _constraint("Oats before gym", "Eat oats two hours before the gym")
    async with _judge() as judge:
        result = await judge.canonicalise(
            _obs("Protein shake within 30 minutes after the gym"), [existing]
        )
    assert result.constraint_uid is None


async def test_an_unrelated_statement_is_new():
    existing = _constraint("Oats before gym", "Eat oats two hours before the gym")
    async with _judge() as judge:
        result = await judge.canonicalise(
            _obs("Never schedule meetings before 13:00"), [existing]
        )
    assert result.constraint_uid is None


async def test_named_weekdays_are_extracted():
    """Measured defect: this rule was served on a Monday."""
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Client attendance days: Go to client on Tuesdays and Thursdays.")
        )
    assert sorted(result.days_of_week) == [1, 3]


async def test_a_single_named_weekday_is_extracted():
    async with _judge() as judge:
        result = await judge.tier(
            _obs(
                "Wednesday revenue-first precedence: On Wednesday, Revenue lane "
                "must run before any build/system cognitive block."
            )
        )
    assert result.days_of_week == [2]


async def test_a_daily_rule_acquires_no_day_filter():
    """The dangerous direction: inventing scoping silently hides a rule."""
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Sleep schedule: Aim to sleep at 23:00 and wake at 07:00.")
        )
    assert result.days_of_week == []
    assert result.start_date is None
    assert result.end_date is None


async def test_an_unscoped_rule_acquires_no_dates():
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Oats Timing: Oats must be consumed exactly 2 hours before the gym.")
        )
    assert result.days_of_week == []
    assert result.start_date is None


async def test_a_sprint_scoped_cap_is_project_class():
    """The C2F family is the reason decay exists."""
    async with _judge() as judge:
        result = await judge.tier(
            _obs(
                "C2F framing cap 15m: C2F framing is capped at 15 minutes and "
                "must end with one success sentence."
            )
        )
    assert result.decay_class is DecayClass.PROJECT


async def test_a_sleep_window_is_permanent():
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Sleep schedule: Aim to sleep at 23:00 and wake at 07:00.")
        )
    assert result.decay_class is DecayClass.PERMANENT


async def test_a_commute_duration_is_seasonal():
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Commute Duration: Commute is always 30 minutes long.")
        )
    assert result.decay_class is DecayClass.SEASONAL


async def test_todays_appointment_is_daily():
    async with _judge() as judge:
        result = await judge.tier(_obs("Hockey game today at 11:45 at VVV"))
    assert result.decay_class is DecayClass.DAILY
