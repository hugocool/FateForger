"""Quality of the two frame judgements against the live model (#251).

Two prompts decide whether the session asks about the sleep window: the
corpus judge (does a saved rule state it?) and the intent interpreter (did the
user just state it?). Their unit tests stub the model and prove the plumbing;
this is what proves the prompts. Every case resamples -- a single draw tests
the model's luck -- and the rate is the assertion.

Runs on the client production builds for the interpreter, so what is measured
is what runs.
"""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import date

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set",
    ),
]

# At n=8 a genuine coin flip clears 7 in ~3.5% of runs.
SAMPLES = 8
THRESHOLD = 7

def _report(results: list) -> str:
    """Every draw, exceptions with their traceback -- a failed eval must say why."""

    lines = []
    for r in results:
        if isinstance(r, BaseException):
            lines.append("".join(traceback.format_exception(r)).rstrip())
        else:
            lines.append(repr(r))
    return "\n---\n".join(lines)


OATS = {"uid": "c-oats", "name": "Oats before gym", "description": "Eat oats two hours before the gym.", "necessity": "should"}
GYM = {"uid": "c-gym", "name": "Gym session", "description": "Goes to the gym at 18:00.", "necessity": "should"}
NO_LATE_MEETINGS = {"uid": "c-meet", "name": "No late meetings", "description": "No meetings after 17:00 on working days.", "necessity": "must"}
BEDTIME = {"uid": "c-bed", "name": "Bedtime", "description": "In bed by 00:30 on weekdays, up at 08:30.", "necessity": "must"}


def _client():
    from fateforger.llm.factory import build_autogen_chat_client

    return build_autogen_chat_client("timeboxing_agent", temperature=0)


def _day():
    from fateforger.agents.timeboxing.session_contracts import PlanningDay

    return PlanningDay.lock_default(
        value=date(2026, 9, 2), timezone="Europe/Amsterdam", lock_revision=1
    )


async def _frame_rate(rows: list[dict]) -> list:
    from fateforger.agents.timeboxing.day_frame import DayFrameJudge

    judge = DayFrameJudge(_client())
    return await asyncio.gather(
        *(
            judge.frame_on_record(day=_day(), constraints=rows, session_key="eval")
            for _ in range(SAMPLES)
        ),
        return_exceptions=True,
    )


@pytest.mark.asyncio
async def test_a_bedtime_rule_is_read_as_the_frame() -> None:
    results = await _frame_rate([OATS, GYM, BEDTIME, NO_LATE_MEETINGS])
    hits = [
        r
        for r in results
        if not isinstance(r, BaseException)
        and r is not None
        and r.value["wake"] == "08:30"
        and r.value["sleep"] == "00:30"
        and "c-bed" in r.value["basis"]
    ]
    assert len(hits) >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_evening_rules_are_not_a_bedtime() -> None:
    """No meetings after 17:00 and gym at 18:00 say nothing about sleep."""

    results = await _frame_rate([OATS, GYM, NO_LATE_MEETINGS])
    misses = [r for r in results if r is None]
    assert len(misses) >= THRESHOLD, _report(results)


def _capture_snapshot(*, pending_frame_question: bool = False):
    from fateforger.agents.timeboxing.session_contracts import (
        FactKind,
        PendingBlocker,
        PlanningSessionSnapshot,
    )

    snapshot = PlanningSessionSnapshot(
        session_key="eval", revision=3, owner_user_id="U1", planning_day=_day()
    )
    if pending_frame_question:
        snapshot = snapshot.model_copy(
            update={
                "pending_blocker": PendingBlocker(
                    requirement_id="skeleton.day_frame",
                    fact_kind=FactKind.DAY_FRAME,
                    options=[],
                )
            }
        )
    return snapshot


async def _interpretations(text: str, snapshot) -> list:
    from fateforger.slack_bot.timeboxing_intents import TimeboxingIntentInterpreter

    interpreter = TimeboxingIntentInterpreter(_client())
    return await asyncio.gather(
        *(interpreter.interpret(text, snapshot) for _ in range(SAMPLES)),
        return_exceptions=True,
    )


def _frames(intent) -> list[dict]:
    from fateforger.agents.timeboxing.session_contracts import (
        FactKind,
        ProvidePlanningFacts,
    )

    if not isinstance(intent, ProvidePlanningFacts):
        return []
    return [
        fact.value
        for fact in intent.facts
        if fact.kind is FactKind.DAY_FRAME and isinstance(fact.value, dict)
    ]


@pytest.mark.asyncio
async def test_the_2026_09_02_correction_is_a_frame_fact() -> None:
    results = await _interpretations(
        "I'll sleep today from 00:30 untill 8:30", _capture_snapshot()
    )
    hits = [
        r
        for r in results
        if any(f.get("wake") == "08:30" and f.get("sleep") == "00:30" for f in _frames(r))
    ]
    assert len(hits) >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_the_2026_09_02_opening_message_states_no_frame() -> None:
    """Activities only. A frame extracted here would be the assumption in disguise."""

    from fateforger.agents.timeboxing.session_contracts import (
        FactKind,
        ProvidePlanningFacts,
    )

    results = await _interpretations(
        "serious c2f work, some finances, validate the agent-in-ysis demos, gym for chest",
        _capture_snapshot(),
    )
    hits = [
        r
        for r in results
        if isinstance(r, ProvidePlanningFacts)
        and not _frames(r)
        and any(f.kind is FactKind.REQUESTED_ACTIVITY for f in r.facts)
    ]
    assert len(hits) >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_bare_times_answer_the_open_frame_question() -> None:
    results = await _interpretations(
        "8:30 to half past midnight", _capture_snapshot(pending_frame_question=True)
    )
    hits = [
        r
        for r in results
        if any(f.get("wake") == "08:30" and f.get("sleep") == "00:30" for f in _frames(r))
    ]
    assert len(hits) >= THRESHOLD, _report(results)
