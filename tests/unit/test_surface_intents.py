from __future__ import annotations

import ast
import inspect
import json
from types import SimpleNamespace
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from fateforger.agents.timeboxing.session_contracts import BlockerOption
from fateforger.slack_bot import surface_intents
from fateforger.slack_bot.surface_intents import (
    CHOOSE_OPTION,
    SurfaceIntentError,
    SurfaceIntentInterpreter,
    SurfaceView,
    narrow_schema,
)


class _Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["go", "none"]


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


ADD = BlockerOption(option_id="add", label="Add to calendar", effect="adds it")


def _view(**overrides) -> SurfaceView:
    base = dict(
        surface_kind="test_surface",
        display_state="draft",
        allowed_decisions=("go", "none"),
    )
    base.update(overrides)
    return SurfaceView(**base)


def test_narrow_schema_is_the_base_when_nothing_is_offered() -> None:
    assert narrow_schema(_Turn, ()) is _Turn


def test_narrow_schema_adds_only_the_offered_ids() -> None:
    narrowed = narrow_schema(_Turn, (ADD,))
    narrowed.model_validate({"decision": CHOOSE_OPTION, "option_id": "add"})
    with pytest.raises(ValidationError):
        narrowed.model_validate({"decision": CHOOSE_OPTION, "option_id": "cancel"})


@pytest.mark.asyncio
async def test_interpret_hands_the_model_the_options_and_returns_the_schema() -> None:
    client = _SchemaOutputClient({"decision": CHOOSE_OPTION, "option_id": "add"})
    interpreter = SurfaceIntentInterpreter(client)

    turn = await interpreter.interpret(
        view=_view(allowed_decisions=("go", "none"), offered_options=(ADD,)),
        user_text="Okay!",
        schema=_Turn,
        prompt_fragment="",
        attribution=("test_intent", "test_intent", "k"),
    )

    assert turn.decision == CHOOSE_OPTION
    assert turn.option_id == "add"
    prompt = json.loads(client.calls[0][0][1].content)
    assert prompt["offered_options"] == [
        {"option_id": "add", "label": "Add to calendar", "effect": "adds it"}
    ]
    assert prompt["allowed_decisions"] == ["go", "none", CHOOSE_OPTION]
    assert prompt["user_text"] == "Okay!"


@pytest.mark.asyncio
async def test_a_decision_outside_the_allowed_set_raises() -> None:
    client = _SchemaOutputClient({"decision": "go"})
    interpreter = SurfaceIntentInterpreter(client)

    with pytest.raises(SurfaceIntentError, match="not allowed"):
        await interpreter.interpret(
            view=_view(allowed_decisions=("none",)),
            user_text="go",
            schema=_Turn,
            prompt_fragment="",
            attribution=("test_intent", "test_intent", "k"),
        )


@pytest.mark.asyncio
async def test_a_surface_that_accepts_nothing_raises_before_any_call() -> None:
    client = _SchemaOutputClient()
    interpreter = SurfaceIntentInterpreter(client)

    with pytest.raises(SurfaceIntentError, match="does not accept"):
        await interpreter.interpret(
            view=_view(allowed_decisions=()),
            user_text="anything",
            schema=_Turn,
            prompt_fragment="",
            attribution=("test_intent", "test_intent", "k"),
        )
    assert client.calls == []


def test_the_module_never_reads_user_text_itself() -> None:
    """CLAUDE.md: the judgement is the model's. No `re`, no substring tests."""

    tree = ast.parse(inspect.getsource(surface_intents))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "re" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"
        if isinstance(node, ast.Compare):
            assert not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops), (
                "membership test in surface_intents.py — if it is over user text, "
                "that is the banned judgement"
            )


@pytest.mark.asyncio
async def test_a_model_failure_arrives_as_a_surface_intent_error() -> None:
    class _Raises:
        async def create(self, messages, *, json_output):  # noqa: ANN001
            raise RuntimeError("model unavailable")

    interpreter = SurfaceIntentInterpreter(_Raises())

    with pytest.raises(SurfaceIntentError) as raised:
        await interpreter.interpret(
            view=_view(),
            user_text="Okay!",
            schema=_Turn,
            prompt_fragment="",
            attribution=("test_intent", "test_intent", "k"),
        )

    assert isinstance(raised.value.__cause__, RuntimeError)
