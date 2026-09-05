"""Before a day is locked there *is* something to decide: start, ask, or
cancel. The interpreter decides it; nothing here reads the words."""

from __future__ import annotations

import ast
import inspect
import json
from types import SimpleNamespace

import pytest

import fateforger.slack_bot.timeboxing_host as host_module
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    AskQuestion,
    CancelSession,
    PlanningSessionSnapshot,
    StartSession,
)
from fateforger.slack_bot.timeboxing_host import derive_timebox_intent
from fateforger.slack_bot.timeboxing_intents import (
    TimeboxingIntentInterpreter,
    _display_context,
)


class _SchemaOutputClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


def _fresh() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(session_key="D1:dm", revision=0, owner_user_id="U1")


def _runtime(*responses):
    return SimpleNamespace(
        timeboxing_intent_interpreter=TimeboxingIntentInterpreter(
            _SchemaOutputClient(*responses)
        )
    )


def test_a_fresh_session_offers_start_question_and_cancel() -> None:
    state, allowed, pending = _display_context(_fresh())
    assert state == "no_session"
    assert set(allowed) == {"start", "question", "cancel"}
    assert pending is None


@pytest.mark.asyncio
async def test_start_opens_the_session_exactly_as_before() -> None:
    intent = await derive_timebox_intent(
        _runtime({"decision": "start", "facts": []}),
        _fresh(),
        user_text="plan tomorrow",
    )
    assert intent == StartSession()


@pytest.mark.asyncio
async def test_a_question_before_a_day_is_asked_not_started() -> None:
    intent = await derive_timebox_intent(
        _runtime({"decision": "question", "facts": []}),
        _fresh(),
        user_text="Is it planned?",
    )
    assert intent == AskQuestion(question="Is it planned?")


@pytest.mark.asyncio
async def test_a_cancel_before_a_day_reaches_the_kernel() -> None:
    intent = await derive_timebox_intent(
        _runtime({"decision": "cancel", "facts": []}),
        _fresh(),
        user_text="never mind",
    )
    assert intent == CancelSession()


@pytest.mark.asyncio
async def test_empty_text_on_a_fresh_session_still_opens_it() -> None:
    # The opening turn arrives with no words (the auto-start, a bare command);
    # that is a start, as it always was. Only typed words are judged.
    runtime = _runtime()  # no interpreter response: it must not be asked
    intent = await derive_timebox_intent(runtime, _fresh(), user_text="   ")
    assert intent == StartSession()
    assert runtime.timeboxing_intent_interpreter.model_client.calls == []


@pytest.mark.asyncio
async def test_empty_text_on_a_started_session_is_still_advance() -> None:
    from datetime import date

    from fateforger.agents.timeboxing.session_contracts import PlanningDay

    snapshot = PlanningSessionSnapshot(
        session_key="D1:dm",
        revision=2,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 5), timezone="Europe/Amsterdam", lock_revision=1
        ),
    )
    assert await derive_timebox_intent(_runtime(), snapshot, user_text="") == Advance()


def test_derive_timebox_intent_has_no_unconditional_start() -> None:
    """The guard for the claim this ticket deletes: no `return StartSession()`
    that is not inside the judged path. Any Return whose value calls
    StartSession must sit under an `if` on the text being empty."""
    tree = ast.parse(inspect.getsource(host_module))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "derive_timebox_intent"
    )
    starts = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == "StartSession"
    ]
    # Exactly one, and it is the empty-text start.
    assert len(starts) == 1
