import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing import agent as timeboxing_agent_mod


class _DummyExtractor:
    def __init__(self, *, model_client, tools):
        self.model_client = model_client
        self.tools = tools

    async def extract_and_upsert_constraint(self, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_extract_and_upsert_constraint_tool_is_strict(monkeypatch):
    async def _fake_get_constraint_mcp_tools():
        return []

    monkeypatch.setattr(
        timeboxing_agent_mod, "get_constraint_mcp_tools", _fake_get_constraint_mcp_tools
    )
    monkeypatch.setattr(
        timeboxing_agent_mod, "NotionConstraintExtractor", _DummyExtractor
    )
    monkeypatch.setattr(
        timeboxing_agent_mod.settings, "notion_timeboxing_parent_page_id", "dummy", raising=False
    )

    agent = timeboxing_agent_mod.TimeboxingFlowAgent.__new__(
        timeboxing_agent_mod.TimeboxingFlowAgent
    )
    agent._constraint_mcp_tools = None
    agent._notion_extractor = None
    agent._constraint_extractor_tool = None
    agent._model_client = object()

    await timeboxing_agent_mod.TimeboxingFlowAgent._ensure_constraint_mcp_tools(agent)

    assert agent._constraint_extractor_tool is not None
    assert agent._constraint_extractor_tool.schema.get("strict") is True

