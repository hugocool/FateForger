# tests/memory/test_eval_anchor_resolution.py
"""Quality of anchor resolution against the live model (#137).

This is the judgement CLAUDE.md's central example got wrong. Jaccard merging
conflated `Work Window` with `Deep Work Block Duration` on this project's own
data — two different concepts, silently merged, forever. The prompt has to
merge spellings of one thing without merging two things that merely share
vocabulary.

Every case resamples: a single draw tests the model's luck, not its behaviour.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from memory.anchor import Anchor
from memory.openrouter_judge import OpenRouterJudge

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]

SAMPLES = 8
THRESHOLD = 7
T0 = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
    )


async def _merge_rate(name: str, existing: list[Anchor], target: str) -> int:
    """How many of SAMPLES draws mapped `name` onto the anchor called target."""
    want = next(a.uid for a in existing if a.name == target)
    async with _judge() as judge:
        results = await asyncio.gather(
            *(judge.resolve_anchors([name], existing) for _ in range(SAMPLES))
        )
    return sum(
        1
        for r in results
        if r.resolutions and r.resolutions[0].anchor_uid == want
    )


async def test_a_respelling_merges_onto_the_existing_anchor():
    existing = [Anchor(name="gym"), Anchor(name="hockey"), Anchor(name="dinner")]
    merged = await _merge_rate("the gym", existing, "gym")
    assert merged >= THRESHOLD, f"{merged}/{SAMPLES} merged 'the gym' onto 'gym'"


async def test_two_concepts_sharing_vocabulary_do_not_merge():
    """The measured failure, restated as an assertion.

    `Work Window` and `Deep Work Block Duration` share a word and are not the
    same anchor. A rule about when work may happen does not govern how long a
    block runs.
    """
    existing = [Anchor(name="Work Window"), Anchor(name="dinner")]
    async with _judge() as judge:
        results = await asyncio.gather(
            *(
                judge.resolve_anchors(["Deep Work Block Duration"], existing)
                for _ in range(SAMPLES)
            )
        )
    kept_apart = sum(
        1 for r in results if r.resolutions and r.resolutions[0].anchor_uid is None
    )
    assert kept_apart >= THRESHOLD, (
        f"only {kept_apart}/{SAMPLES} kept 'Deep Work Block Duration' distinct "
        f"from 'Work Window' — this is the conflation CLAUDE.md cites"
    )


async def test_a_specific_activity_does_not_merge_into_its_general_category():
    """Hockey is not sport.

    They are different anchors and the relationship between them is an edge,
    not an identity. Merging here would collapse the taxonomy into its roots
    and make the graph unable to distinguish anything.
    """
    existing = [Anchor(name="sport"), Anchor(name="dinner")]
    async with _judge() as judge:
        results = await asyncio.gather(
            *(judge.resolve_anchors(["hockey"], existing) for _ in range(SAMPLES))
        )
    kept_apart = sum(
        1 for r in results if r.resolutions and r.resolutions[0].anchor_uid is None
    )
    assert kept_apart >= THRESHOLD, (
        f"only {kept_apart}/{SAMPLES} kept 'hockey' distinct from 'sport'"
    )
