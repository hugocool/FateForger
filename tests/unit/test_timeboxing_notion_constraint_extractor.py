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


@pytest.mark.asyncio
async def test_extract_and_upsert_raises_on_invalid_payload() -> None:
    class _BadAgentTool:
        async def run_json(self, *_args: Any, **_kwargs: Any):
            return SimpleNamespace(chat_message=SimpleNamespace(content="not-json"))

    extractor = extractor_mod.NotionConstraintExtractor.__new__(
        extractor_mod.NotionConstraintExtractor
    )
    extractor._agent_tool = _BadAgentTool()

    handoff = extractor_mod.ConstraintHandoff(
        planned_date=extractor_mod.date(2026, 2, 17),
        timezone="Europe/Amsterdam",
        stage_id="CollectConstraints",
        user_utterance="No meetings before 10",
        triggering_suggestion=None,
        impacted_event_types=["M"],
        suggested_tags=["meetings"],
        session_id="s1",
        decision_scope="profile",
    )

    with pytest.raises(RuntimeError, match="Constraint extractor returned invalid output"):
        await extractor.extract_and_upsert(handoff)
