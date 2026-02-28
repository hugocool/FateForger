from __future__ import annotations

import pytest

from fateforger.agents.timeboxing import nlu


def test_constraint_interpreter_uses_schema_prompt_text_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAssistantAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(nlu, "AssistantAgent", _FakeAssistantAgent)

    nlu.build_constraint_interpreter(model_client=object())

    assert captured.get("output_content_type") is None
    system_message = str(captured.get("system_message") or "")
    assert "ConstraintInterpretation JSON Schema" in system_message
    assert "\"additionalProperties\"" in system_message


def test_planned_date_interpreter_keeps_structured_output(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAssistantAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(nlu, "AssistantAgent", _FakeAssistantAgent)

    nlu.build_planned_date_interpreter(model_client=object())

    assert captured.get("output_content_type") is nlu.PlannedDateResult
