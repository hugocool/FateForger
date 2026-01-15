import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.flow import build_timeboxing_flow
from fateforger.agents.timeboxing.timebox import Timebox
from autogen_ext.models.openai import OpenAIChatCompletionClient


def test_draft_node_is_structured():
    model_client = OpenAIChatCompletionClient(model="gpt-4o-mini", api_key="test")
    flow = build_timeboxing_flow(model_client)
    participants = {agent.name: agent for agent in flow._participants}  # type: ignore[attr-defined]
    draft = participants.get("DraftTimebox")
    assert draft is not None
    assert getattr(draft, "_output_content_type", None) is Timebox


def test_system_prompt_included():
    model_client = OpenAIChatCompletionClient(model="gpt-4o-mini", api_key="test")
    flow = build_timeboxing_flow(model_client)
    participants = {agent.name: agent for agent in flow._participants}  # type: ignore[attr-defined]
    hydrate = participants.get("HydrateContext")
    assert hydrate is not None
    system_messages = getattr(hydrate, "_system_messages", [])
    assert system_messages, "Expected system messages to be configured"
    content = system_messages[0].content
    assert "Professional Time-Boxing Agent" in content
