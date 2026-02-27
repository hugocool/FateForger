from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

import fateforger.agents.timeboxing.agent as timeboxing_agent_mod
from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent

@pytest.mark.asyncio
async def test_stage_collect_constraints_agent_keeps_search_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 1 keeps search available while remaining deterministic-first by prompt policy."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._stage_agents = {}
    agent._decision_agent = None
    agent._summary_agent = None
    agent._review_commit_agent = None
    agent._model_client = object()
    agent._constraint_search_tool = None

    captured_tools: dict[str, object] = {}

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
                output_content_type,
                system_message,
                reflect_on_tool_use,
                max_tool_iterations,
            )
            captured_tools[name] = tools

    monkeypatch.setattr(timeboxing_agent_mod, "AssistantAgent", _FakeAssistantAgent)
    monkeypatch.setattr(
        timeboxing_agent_mod,
        "assert_strict_tools_for_structured_output",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_build_constraint_search_tool",
        lambda _self: "search_constraints_tool",
    )
    await TimeboxingFlowAgent._ensure_stage_agents(agent)

    assert captured_tools["StageCollectConstraints"] == ["search_constraints_tool"]
    assert captured_tools["StageCaptureInputs"] == ["search_constraints_tool"]
