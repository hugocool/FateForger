# tests/integration/test_eval_planning_card_intent.py
"""Quality of the planning-card reply interpreter against the live model.

Unit tests stub the model and prove the plumbing; this proves the prompt and
the offered-controls context. Every case resamples -- one draw tests the
model's luck -- and the rate is the assertion. No temperature pin.
"""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import datetime, timezone

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"),
]

SAMPLES = 8
THRESHOLD = 7


def _report(results: list) -> str:
    lines = []
    for r in results:
        if isinstance(r, BaseException):
            lines.append("".join(traceback.format_exception(r)).rstrip())
        else:
            lines.append(repr(r))
    return "\n---\n".join(lines)


def _draft(status_name: str = "DRAFT"):
    from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload

    return EventDraftPayload(
        draft_id="draft_eval",
        user_id="U1",
        channel_id="D1",
        message_ts="1.0",
        calendar_id="primary",
        event_id="ffplanningeval",
        title="Daily planning session",
        description="Plan tomorrow's priorities and prep for shutdown.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 9, 3, 8, 38, tzinfo=timezone.utc).isoformat(),  # 10:38 local
        duration_min=30,
        status=DraftStatus[status_name],
        event_url=None,
        last_error="calendar unreachable" if status_name == "FAILURE" else None,
    )


async def _presses(text: str, *, status_name: str = "DRAFT", strip_effect: bool = False) -> list:
    from fateforger.llm.factory import build_autogen_chat_client
    from fateforger.slack_bot.planning_surface import (
        PLANNING_PROMPT_FRAGMENT,
        InterpretedPlanningTurn,
        bind,
        planning_view,
    )
    from fateforger.slack_bot.surface_intents import SurfaceIntentInterpreter

    view = planning_view(_draft(status_name))
    if strip_effect:
        # The break-it check: without the effect text the model has only a label.
        view = view.model_copy(
            update={"offered_options": tuple(o.model_copy(update={"effect": "-"}) for o in view.offered_options)}
        )
    interpreter = SurfaceIntentInterpreter(build_autogen_chat_client("planner_agent"))

    async def one():
        turn = await interpreter.interpret(
            view=view,
            user_text=text,
            schema=InterpretedPlanningTurn,
            prompt_fragment=PLANNING_PROMPT_FRAGMENT,
            attribution=("planning_card_intent_interpreter", "planning_card_intent", "eval"),
        )
        return bind(turn)

    return await asyncio.gather(*(one() for _ in range(SAMPLES)), return_exceptions=True)


def _count(results: list, *, kind: str | None, time: str | None = None) -> int:
    hits = 0
    for r in results:
        if isinstance(r, BaseException):
            continue
        if kind is None:
            hits += r is None
        else:
            hits += r is not None and r.kind == kind and r.selected_time == time
    return hits


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Okay!", "yes", "sure, do it"])
async def test_consent_is_the_add_press(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind="add") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["no, let's do 13:45", "13:45"])
async def test_a_time_with_consent_updates_and_adds(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind="update_time_and_add", time="13:45") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_a_time_without_consent_only_updates() -> None:
    results = await _presses("make it 17:00 but don't add yet")
    assert _count(results, kind="update_time", time="17:00") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["why 10:38?", "plan tomorrow for me", "later"])
async def test_a_non_press_is_none(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind=None) >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_try_again_on_a_failed_card_is_retry() -> None:
    results = await _presses("try again", status_name="FAILURE")
    assert _count(results, kind="retry") >= THRESHOLD, _report(results)
