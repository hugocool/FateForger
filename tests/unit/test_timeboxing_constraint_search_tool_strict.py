from __future__ import annotations

from fateforger.agents.timeboxing import agent as timeboxing_agent_mod


def test_constraint_search_tool_is_strict() -> None:
    """Ensure stage-gating search tool uses strict function-tool schema."""
    agent = timeboxing_agent_mod.TimeboxingFlowAgent.__new__(
        timeboxing_agent_mod.TimeboxingFlowAgent
    )
    agent._constraint_memory_client = None
    tool = timeboxing_agent_mod.TimeboxingFlowAgent._build_constraint_search_tool(agent)

    assert tool.schema.get("strict") is True
    params = tool.schema.get("parameters", {})
    required = set(params.get("required", []))
    assert {"queries", "planned_date", "stage"} == required
