"""Does the edit judgement actually separate an edit from a rule? (#287)

The unit suite proves the plumbing: an `is_edit` answer suppresses. It cannot
prove the prompt asks a question the model can answer, and CLAUDE.md is
explicit that a green unit suite says nothing about judgement quality.

Every case resamples. A prompt fix validated by one passing call has not been
validated: `test_a_sprint_scoped_cap_is_project_class` passed first run and
then returned the other answer eight times in nine.

The two edits below are the real sentences from 2026-09-03 that became
standing profile rules. The rules below are the near neighbours that must
survive -- they name durations and blocks in the same breath, which is what
makes this a discrimination problem rather than a keyword one.
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
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]

T0 = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc)

SAMPLES = 8
THRESHOLD = 7


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
    )


def _observation(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="eval",
        observed_at=T0,
    )


async def _rate(text: str) -> int:
    """How many of SAMPLES draws called it an edit. Concurrent (CLAUDE.md)."""
    async with _judge() as judge:
        answers = await asyncio.gather(
            *(judge.edit(_observation(text)) for _ in range(SAMPLES))
        )
    return sum(1 for a in answers if a.is_edit)


EDITS = [
    # The two that reached the corpus as durable rules.
    "Make the finances block 30 minutes instead of 45, everything else stays",
    "Move deep work block 30 minutes earlier and add a 15-minute buffer after it",
    # Same shape, other wordings.
    "drop the second admin block",
    "push everything after lunch back by half an hour",
]

RULES = [
    # The meta prompt's own example of a schedule rule, which must survive.
    "Deep Work blocks are usually 2 hours long",
    # Near neighbours: durations and block names, but true with no plan up.
    "I never want more than two deep work blocks in a day",
    "Always leave 15 minutes between meetings",
    "I go to the gym at 18:00",
    # A dated one-off is not an edit either; it gets stored and dated.
    "No meetings this Thursday",
]


@pytest.mark.parametrize("text", EDITS)
async def test_an_instruction_to_change_the_plan_reads_as_an_edit(text: str) -> None:
    called_edit = await _rate(text)
    assert called_edit >= THRESHOLD, (
        f"{text!r} was called an edit in only {called_edit}/{SAMPLES} draws; "
        "this is the class that reached the production corpus as a rule"
    )


@pytest.mark.parametrize("text", RULES)
async def test_a_standing_rule_is_not_read_as_an_edit(text: str) -> None:
    called_edit = await _rate(text)
    assert called_edit <= SAMPLES - THRESHOLD, (
        f"{text!r} was called an edit in {called_edit}/{SAMPLES} draws; "
        "suppressing this class would silently stop memory learning rules"
    )
