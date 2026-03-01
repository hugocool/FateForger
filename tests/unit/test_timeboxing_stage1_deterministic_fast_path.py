from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

import fateforger.agents.timeboxing.agent as timeboxing_agent_mod
from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import (
    CAPTURE_INPUTS_PROMPT,
    COLLECT_CONSTRAINTS_PROMPT,
    DECISION_PROMPT,
    REVIEW_COMMIT_PROMPT,
    TIMEBOX_SUMMARY_PROMPT,
    StageDecision,
    StageGateOutput,
)
from fateforger.core.config import settings


@pytest.mark.asyncio
async def test_stage_collect_constraints_agent_keeps_search_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 1 keeps search available while remaining deterministic-first by prompt policy.

    Validates that _build_one_shot_agent forwards tools correctly to stages 1/2
    and uses schema-in-prompt (output_content_type=None) for summary/review agents.
    """
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._model_client = object()
    agent._constraint_search_tool = None

    captured_tools: dict[str, object] = {}
    captured_output_types: dict[str, object] = {}

    class _FakeAssistantAgent:
        def __init__(
            self,
            *,
            name: str,
            model_client,
            tools,
            output_content_type,
            system_message: str,
            reflect_on_tool_use: bool,
            max_tool_iterations: int,
        ) -> None:
            _ = (
                model_client,
                system_message,
                reflect_on_tool_use,
                max_tool_iterations,
            )
            captured_tools[name] = tools
            captured_output_types[name] = output_content_type

    monkeypatch.setattr(timeboxing_agent_mod, "AssistantAgent", _FakeAssistantAgent)
    monkeypatch.setattr(
        timeboxing_agent_mod,
        "assert_strict_tools_for_structured_output",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(settings, "notion_timeboxing_parent_page_id", "parent")

    fake_search_tool = "search_constraints_tool"

    # Stages 1 and 2 carry the constraint search tool.
    agent._build_one_shot_agent(
        "StageCollectConstraints",
        COLLECT_CONSTRAINTS_PROMPT,
        StageGateOutput,
        tools=[fake_search_tool],
        max_tool_iterations=2,
    )
    agent._build_one_shot_agent(
        "StageCaptureInputs",
        CAPTURE_INPUTS_PROMPT,
        StageGateOutput,
        tools=[fake_search_tool],
        max_tool_iterations=3,
    )
    # Summary and ReviewCommit use schema-in-prompt (output_content_type=None).
    agent._build_one_shot_agent(
        "StageTimeboxSummary",
        TIMEBOX_SUMMARY_PROMPT,
        StageGateOutput,
        structured_output=False,
    )
    agent._build_one_shot_agent(
        "StageReviewCommit",
        REVIEW_COMMIT_PROMPT,
        StageGateOutput,
        structured_output=False,
    )
    # Decision uses structured output.
    agent._build_one_shot_agent("StageDecision", DECISION_PROMPT, StageDecision)

    assert captured_tools["StageCollectConstraints"] == [fake_search_tool]
    assert captured_tools["StageCaptureInputs"] == [fake_search_tool]
    assert captured_output_types["StageCollectConstraints"] is not None
    assert captured_output_types["StageCaptureInputs"] is not None
    assert captured_output_types["StageTimeboxSummary"] is None
    assert captured_output_types["StageReviewCommit"] is None
    assert captured_output_types["StageDecision"] is not None
