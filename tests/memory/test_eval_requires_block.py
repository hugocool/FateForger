# tests/memory/test_eval_requires_block.py
"""Does the sixth question separate 'a block must exist' from rules about
blocks (#212, spec §1 evals)?

Eight draws per case; the rate is the assertion. A prompt validated by one
green call has not been validated (CLAUDE.md). The ambiguous case is recorded,
not asserted: its rate in the docstring is what a later prompt change compares
against.

Measured on google/gemini-3.6-flash at 8 draws:
  positives 8/8 (the promotion's own rule), 0/8 (the end-of-day closure rule);
  negatives 8/8, 8/8, 8/8, 8/8 and ambiguous 0/8 as measured when this was
  written, not re-measured since.

**The closure case is failing on purpose and the number is the finding.**

It scored 8/8 once, and the reason was that REQUIRES_BLOCK_PROMPT contained a
description of it: a paragraph about "a short end-of-day routine that closes
out today and sets up for what comes next -- reviewing progress, updating
status, carrying work forward". That is the eval sentence restated as a rule,
so the eval was measuring whether the model can recognise a case it had been
handed, which is not what the case is for. The paragraph was replaced with the
same instruction carried by differently shaped examples (a weekly review, a
morning check-in), and the rate went 8/8 -> 0/8. Measured 2026-09-04, both at
8 draws: with the old paragraph and the new example sentence, 8/8 -- so the
discriminator was the paraphrase, not the example.

What the model says when it is not handed the answer, consistently across all
eight draws: an end-of-day block for updating artifact links and board status
is administrative closure, and its purpose differs from planning, which it
reads as deciding how time gets spent. That is a defensible reading. Spec §1
names this statement a `planning` positive; either the spec's expectation or
the kind's definition has to give, and the threshold is left at 7 so the
disagreement stays visible instead of being absorbed.
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
