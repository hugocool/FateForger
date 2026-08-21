# tests/memory/test_eval_canonicalise.py
"""Quality of the canonicalise judgement against the live model (#169).

The failure this encodes was found in Hugo's real store: "Lunch: Lunch break"
merged into "Daily Meals: include breakfast, lunch and dinner every day",
producing a constraint with two observations asserting different things. A
later re-projection then overwrote the broader rule with the narrower one, and
the store ended up with two lunch rules and no meals rule.

Measured before the fix, the merge happened **8 times out of 8**. The prompt
had one discriminator — different rules about the same topic are not one rule
— and no instruction about part and whole, so a rule naming lunch read as a
restatement of the rule containing lunch.

This is the failure CLAUDE.md cites as justification for the no-pattern-
matching rule (`Work Window` conflated with `Deep Work Block Duration`)
arriving **by model rather than by Jaccard**. Moving the judgement to an LLM
removed the guarantee that a dumb comparison would be wrong; it did not remove
the failure mode. The fix is the same one `resolve_anchors` got: name the
distinction the model is failing to draw.

Every case resamples. Ground truth is taken from the real corpus rather than
invented, so a regression here is a regression against something that actually
happened.
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

SAMPLES = 8
THRESHOLD = 7
T0 = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


class _Candidate:
    """Matches the ConstraintLike protocol the judge expects."""

    def __init__(self, uid: str, name: str, description: str) -> None:
        self.uid, self.name, self.description = uid, name, description


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


async def _rate(text: str, candidates: list[_Candidate], expected: str | None) -> int:
    async with _judge() as judge:
        results = await asyncio.gather(
            *(judge.canonicalise(_obs(text), candidates) for _ in range(SAMPLES))
        )
    return sum(1 for r in results if r.constraint_uid == expected)


MEALS = _Candidate(
    "c-meals",
    "Daily Meals",
    "Daily Meals: Include breakfast, lunch, and dinner every day.",
)
EVENING = _Candidate(
    "c-evening",
    "Evening Ritual",
    "Evening Ritual: dinner, tidy up, reading, then bed",
)
DEEP_WORK = _Candidate(
    "c-dw",
    "Deep Work Block Duration",
    "Deep Work (DW) blocks are usually 2 hours long",
)
WORK_WINDOW = _Candidate(
    "c-ww", "Work Window", "Work Window: the hours in which work may be scheduled"
)


@pytest.mark.parametrize(
    "text,candidates",
    [
        ("Lunch: Lunch break", [MEALS]),
        ("Dinner at 19:00", [EVENING]),
    ],
)
async def test_a_part_does_not_merge_into_the_whole_containing_it(
    text, candidates
):
    """The exact regression, and the one the seeding run predicted.

    Merging a rule about lunch into the rule about all three meals loses
    breakfast and dinner outright. The second case — dinner folding into the
    evening ritual — was recorded as a structural gap during seeding, before
    anyone connected it to this judgement.
    """
    correct = await _rate(text, candidates, None)
    assert correct >= THRESHOLD, (
        f"{correct}/{SAMPLES} kept {text!r} distinct from "
        f"{candidates[0].name!r}; merging a part into its whole discards the "
        f"rest of the whole"
    )


async def test_a_specific_rule_does_not_merge_into_its_general_category():
    """How long a block runs is not when blocks may be scheduled."""
    correct = await _rate(
        "Deep-work duration cap: blocks bounded to 60-90 minutes",
        [WORK_WINDOW],
        None,
    )
    assert correct >= THRESHOLD, f"{correct}/{SAMPLES} kept them distinct"


@pytest.mark.parametrize(
    "text",
    [
        "DW Block Duration: Deep Work blocks are usually 2 hours long",
        "deep_work_duration: Deep Work (DW) blocks are usually 2 hours long",
    ],
)
async def test_a_genuine_restatement_still_merges(text):
    """The other half, and the reason this cannot be fixed by refusing to merge.

    The real store holds eleven observations of this one rule across eight
    surface spellings. A prompt made cautious enough to never merge a part
    into a whole must still recognise these, or the store fragments into a
    copy per phrasing — which is the failure the canonicalisation layer exists
    to prevent.
    """
    correct = await _rate(text, [DEEP_WORK, MEALS], "c-dw")
    assert correct >= THRESHOLD, f"{correct}/{SAMPLES} merged {text!r} correctly"


async def test_two_rules_about_one_activity_stay_separate():
    """Pre-existing behaviour, asserted so the new instructions cannot cost it."""
    correct = await _rate(
        "Work Stop Constraint: User must stop working before going to the gym",
        [_Candidate("c-gym", "Gym Session", "Gym Session: user goes to the gym at 18:00")],
        None,
    )
    assert correct >= THRESHOLD, f"{correct}/{SAMPLES} kept them distinct"
