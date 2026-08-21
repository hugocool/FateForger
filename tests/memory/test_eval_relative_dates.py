# tests/memory/test_eval_relative_dates.py
"""Dating a one-off against the day it was said.

Session constraints came back with empty applicability — "tomorrow" produced
no dates — so a session rule expired by *last mention* rather than by the day
it is about, and a Monday statement about Thursday died on Wednesday. That is
the difference between decay being a real expiry mechanism and a crude timer,
and it matters more now that the session tier is the working memory of a live
planning thread.

The cause was not a weak prompt. **The judge was only ever shown
`observation.text`**, so "tomorrow" was unresolvable in principle — the model
had no reference point. Recorded in the blindspot sweep as "no judge prompt
receives channel or today's date" and never closed until now.

Supplying a timestamp the system already holds is not a judgement about
meaning. Resolving the phrase against it is, which is why the model does that
half.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone

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

SAMPLES = 4
SAID_ON = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)   # a Friday


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
        observed_at=SAID_ON,
    )


async def _judgements(text: str):
    async with _judge() as judge:
        return await asyncio.gather(
            *(judge.tier(_obs(text)) for _ in range(SAMPLES))
        )


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("dentist appointment tomorrow at 14:00", date(2026, 8, 22), date(2026, 8, 22)),
        ("moving house on the 3rd of September", date(2026, 9, 3), date(2026, 9, 3)),
    ],
)
async def test_a_relative_date_resolves_against_the_day_it_was_said(
    text, start, end
):
    results = await _judgements(text)
    correct = sum(1 for r in results if r.start_date == start and r.end_date == end)
    assert correct == SAMPLES, (
        f"{correct}/{SAMPLES} dated {text!r} as {start}..{end}; got "
        f"{[(r.start_date, r.end_date) for r in results]}"
    )


async def test_a_span_resolves_to_a_span():
    """Said on a Friday, "next week" is the Monday to the Sunday after it."""
    results = await _judgements("I am away next week")
    dated = sum(1 for r in results if r.start_date and r.end_date)
    assert dated == SAMPLES
    assert all(r.start_date > SAID_ON.date() for r in results)
    assert all(r.end_date >= r.start_date for r in results)


@pytest.mark.parametrize(
    "text",
    [
        "deep work blocks are usually 2 hours",
        "sleep at 23:00",
    ],
)
async def test_a_standing_rule_still_gets_no_dates(text):
    """The other half, and the one a date in the prompt could easily cost.

    Handing the model a date invites it to attach one to everything. A
    standing rule that acquires an end date silently stops applying, which is
    the failure this whole tier exists to avoid.
    """
    results = await _judgements(text)
    dated = [r for r in results if r.start_date or r.end_date]
    assert not dated, (
        f"{len(dated)}/{SAMPLES} invented scoping for a standing rule: "
        f"{[(r.start_date, r.end_date) for r in dated]}"
    )
