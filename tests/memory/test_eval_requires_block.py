# tests/memory/test_eval_requires_block.py
"""Does the sixth question separate 'a block must exist' from rules about
blocks (#212, spec §1 evals)?

Eight draws per case; the rate is the assertion. A prompt validated by one
green call has not been validated (CLAUDE.md). The ambiguous case is recorded,
not asserted: its rate in the docstring is what a later prompt change compares
against.

Measured on google/gemini-3.6-flash at 8 draws when this was written (after one
discriminator addition to REQUIRES_BLOCK_PROMPT -- see below):
  positives 8/8, 8/8; negatives 8/8, 8/8, 8/8, 8/8; ambiguous 0/8

The end-of-day closure case was the reason for that addition. The prompt
originally described only when a block is *required* (existence), with
nothing telling the model how to pick *which* registered kind an existence
requirement worded differently from the kind's name should map to. Against
["planning", "sleep"] the closure statement scored 0/8 -- every draw answered
null because "closure"/"review" is not the literal word "planning", even
though spec §1's evals name this exact statement as a `planning` positive.
Adding one paragraph telling the model to match by what the session is FOR,
not by whether it repeats the kind's word, took it to 8/8 with no change to
the other six cases.
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
KINDS = ["planning", "sleep"]


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _obs(text: str) -> Observation:
    return Observation(text=text, channel=Channel.PLANNING, provenance=Provenance.OBSERVED,
                       session_id="eval", observed_at=T0)


async def _rate(text: str, slug: str | None) -> tuple[int, list[str]]:
    """How many of SAMPLES draws answered `slug`, and why."""
    async with _judge() as judge:
        results = await asyncio.gather(*(judge.requires_block(_obs(text), KINDS) for _ in range(SAMPLES)))
    return sum(r.slug == slug for r in results), [f"{r.slug}: {r.rationale}" for r in results]


@pytest.mark.parametrize("text", [
    "Every working day has a planning session in which the next day is timeboxed.",
    "End-of-day closure block: reserve 15-20 minutes at the end of the workday to update artifact links and board status.",
])
async def test_a_rule_that_requires_a_planning_block_names_the_kind(text):
    hits, rationales = await _rate(text, "planning")
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} chose planning for {text!r}; {rationales}"


@pytest.mark.parametrize("text", [
    "Deep work blocks run 90-120 minutes.",
    "Oats must be consumed exactly 2 hours before the gym session.",
    "Avoid back-to-back blocks of the same type; alternate deep and shallow work.",
    "No meetings before 13:00.",
])
async def test_a_rule_about_blocks_requires_none(text):
    hits, rationales = await _rate(text, None)
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} answered null for {text!r}; {rationales}"


async def test_the_ambiguous_timeboxing_sentence_is_recorded_not_asserted():
    """'I timebox my day by allocating fixed blocks' says he timeboxes; it does
    not say a session must be on the plan. Whatever the model answers is data:
    print it so the run's log carries the rate. The assertion is over the
    transport's own contract, not the count: every draw's slug must be one of
    the offered kinds or null (requires_block's own verification already
    guarantees this -- this test checks it held, not the rate it produced)."""
    async with _judge() as judge:
        text = "I timebox my day by allocating fixed blocks for tasks and activities."
        results = await asyncio.gather(
            *(judge.requires_block(_obs(text), KINDS) for _ in range(SAMPLES))
        )
    hits = sum(r.slug == "planning" for r in results)
    rationales = [f"{r.slug}: {r.rationale}" for r in results]
    print(f"ambiguous: {hits}/{SAMPLES} chose planning; {rationales}")
    assert all(r.slug in KINDS or r.slug is None for r in results)
