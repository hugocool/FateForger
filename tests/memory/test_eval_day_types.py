# tests/memory/test_eval_day_types.py
"""Does the tier question scope a rule to kinds of day when the statement does,
and leave it unscoped when it does not (#212, spec §1)?

Eight draws per case; the rate is the assertion. Measured on
google/gemini-3.6-flash at 8 draws when written: working 8/8, vacation 8/8,
unscoped 8/8.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from memory.models import Channel, Observation, Provenance
from memory.openrouter_judge import OpenRouterJudge

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"),
]

T0 = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
SAMPLES = 8
THRESHOLD = 7


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _obs(text: str) -> Observation:
    return Observation(text=text, channel=Channel.PLANNING, provenance=Provenance.OBSERVED,
                       session_id="eval", observed_at=T0)


async def _rate(text: str, expected: list[str]) -> tuple[int, list[list[str]]]:
    async with _judge() as judge:
        results = await asyncio.gather(*(judge.tier(_obs(text)) for _ in range(SAMPLES)))
    return sum(sorted(r.day_types) == sorted(expected) for r in results), [r.day_types for r in results]


@pytest.mark.parametrize("text,expected", [
    ("Every working day has a planning session in which the next day is timeboxed.", ["working"]),
    ("When I'm on holiday I sleep in until 09:00.", ["vacation"]),
    ("Sleep at 23:00 and wake at 07:00.", []),
])
async def test_day_types_follow_the_statements_scoping(text, expected):
    hits, answers = await _rate(text, expected)
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} answered {expected} for {text!r}; got {answers}"
