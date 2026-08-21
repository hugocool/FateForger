# tests/memory/test_eval_necessity.py
"""Quality of the necessity judgement against the live model (#156).

Every case here resamples. A single draw against a sampled model tests the
model's luck, not its behaviour — `test_a_sprint_scoped_cap_is_project_class`
passed on its first run and then returned the wrong answer eight times out of
nine, because the prompt named a category without giving the model anything to
key off. The rate is the assertion.

What makes this judgement hard is that firmness of wording anti-correlates
with what these cases are testing: the emphatic ones are preferences and the
casual ones are obligations. A prompt that follows the wording gets both
families backwards, which is precisely how necessity ended up MUST on 36 of
37 live constraints.

Measured on google/gemini-3.6-flash, 8 draws per case, at the time this was
written. The new question separates the families completely; the signal it
replaced inverts three of the four cases and answers MUST to three of four,
which is the 36-of-37 shape in miniature:

    case                                  is_declaration   is_binding   truth
    I collect my daughter at 15:00             8/8 MUST     8/8 MUST    boundary
    oh and I have got the school run at 3      0/8 SHOULD   8/8 MUST    boundary
    I ALWAYS start with deep work              8/8 MUST     0/8 SHOULD  preference
    I prefer to keep Fridays clear             8/8 MUST     0/8 SHOULD  preference
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

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)

# Enough draws to tell a robust judgement from a coin flip, few enough to stay
# runnable. At n=8 a genuine 50/50 clears 7 in only 3.5% of runs, so the
# threshold below does not pass by luck.
SAMPLES = 8
THRESHOLD = 7


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


async def _rate(text: str) -> tuple[int, list[str]]:
    """How many of SAMPLES draws called this binding, and why."""
    async with _judge() as judge:
        results = await asyncio.gather(
            *(judge.necessity(_obs(text)) for _ in range(SAMPLES))
        )
    return sum(r.is_binding for r in results), [r.rationale for r in results]


@pytest.mark.parametrize(
    "text",
    [
        "I collect my daughter from school at 15:00",
        "my physio appointment is Thursday at 14:00 and it took a month to get",
        "oh and I've got the school run at 3",
    ],
)
async def test_an_obligation_to_another_person_is_binding(text):
    """Casual wording included deliberately: the third case states a real
    obligation in throwaway terms, which is the shape is_declaration missed."""
    binding, rationales = await _rate(text)
    assert binding >= THRESHOLD, (
        f"{binding}/{SAMPLES} binding for {text!r}; rationales: {rationales}"
    )


@pytest.mark.parametrize(
    "text",
    [
        "I ALWAYS start the day with deep work, never email first",
        "I really don't like meetings in the afternoon",
        "I prefer to keep Fridays clear for admin",
    ],
)
async def test_an_emphatically_stated_preference_is_not_binding(text):
    """The family that made necessity a constant.

    All three are declarations — is_declaration is true for every one — and
    none of them is a boundary. Breaking any produces a worse day, not a
    broken one.
    """
    binding, rationales = await _rate(text)
    assert SAMPLES - binding >= THRESHOLD, (
        f"{binding}/{SAMPLES} binding for {text!r}; rationales: {rationales}"
    )


async def test_the_two_families_actually_separate():
    """The property #156 needs and the old wiring could not deliver.

    Filtering on necessity has to divide the store. A judgement that answers
    the same way to both families is useless however defensible each answer
    looks alone — which is what 36-of-37 MUST was.
    """
    boundary, _ = await _rate("I collect my daughter from school at 15:00")
    preference, _ = await _rate("I ALWAYS start the day with deep work")
    assert boundary > preference, (
        f"binding rate did not separate the families: boundary={boundary}, "
        f"preference={preference} out of {SAMPLES}"
    )
