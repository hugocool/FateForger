from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fateforger.agents.timeboxing import notion_constraint_extractor as extractor_mod


def _sample_payload() -> dict[str, Any]:
    """Return a minimal valid `ConstraintExtractionOutput` payload."""
    return {
        "constraint_record": {
            "name": "No meetings before 10",
            "description": "Avoid meetings before 10:00 on weekdays.",
            "necessity": "should",
            "status": "proposed",
            "source": "user",
            "scope": "profile",
            "applicability": {"timezone": "Europe/Amsterdam"},
            "lifecycle": {"supersedes_uids": []},
            "payload": {
                "rule_kind": "avoid_window",
                "scalar_params": {},
                "windows": [
                    {
                        "kind": "avoid",
                        "start_time_local": "08:00",
                        "end_time_local": "10:00",
                    }
                ],
            },
            "applies_stages": ["CollectConstraints"],
            "applies_event_types": ["M"],
            "topics": ["meetings"],
        }
    }


def test_build_constraint_extractor_agent_omits_output_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure extractor agent is built without OpenAI parse-mode output typing."""
    captured_kwargs: dict[str, Any] = {}

    class _AssistantAgentStub:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(extractor_mod, "AssistantAgent", _AssistantAgentStub)
    agent = extractor_mod.build_constraint_extractor_agent(
        model_client=object(), tools=[]
    )

    assert isinstance(agent, _AssistantAgentStub)
    assert "output_content_type" not in captured_kwargs


def test_parse_constraint_extraction_response_accepts_fenced_json() -> None:
    """Ensure extractor parsing accepts JSON wrapped in markdown code fences."""
    payload = _sample_payload()
    text = f"```json\n{extractor_mod.json.dumps(payload)}\n```"
    response = SimpleNamespace(
        chat_message=SimpleNamespace(content=text),
    )

    parsed = extractor_mod._parse_constraint_extraction_response(response)

    assert parsed.constraint_record.name == "No meetings before 10"
    assert parsed.constraint_record.payload.rule_kind == "avoid_window"
