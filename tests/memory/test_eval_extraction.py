# tests/memory/test_eval_extraction.py
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from memory.models import Channel, Observation, Provenance, Tier
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
    result = await _judge().anchors(_obs("Eat oats 2 hours before going to the gym"))
    assert "gym" in [a.lower() for a in result.anchors]


async def test_a_real_preference_mentioning_a_session_is_not_meta():
    """The marker list would have suppressed this permanently."""
    result = await _judge().meta(_obs("Gym Session — user goes to the gym at 18:00"))
    assert result.is_meta is False


async def test_interaction_chatter_is_meta():
    result = await _judge().meta(
        _obs("The user wants to begin the timeboxing session immediately")
    )
    assert result.is_meta is True


async def test_a_standing_rule_is_durable_and_a_declaration():
    result = await _judge().tier(_obs("I never schedule meetings before 13:00"))
    assert result.tier is Tier.DURABLE
    assert result.is_declaration is True


async def test_todays_appointment_is_session_scoped():
    result = await _judge().tier(_obs("Hockey game today at 11:45 at VVV"))
    assert result.tier is Tier.SESSION
