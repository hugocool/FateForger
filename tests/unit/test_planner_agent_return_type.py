import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import ToolCallSummaryMessage, TextMessage
from autogen_core import CancellationToken, MessageContext

from fateforger.agents.schedular.agent import PlannerAgent


class _DummyHaunt:
    def register_agent(self, *args, **kwargs):
        return None

    async def record_envelope(self, *args, **kwargs):
        return None


class _DummyAssistant:
    def __init__(self, chat_message):
        self._chat_message = chat_message

    async def on_messages(self, *args, **kwargs):
        return type("Resp", (), {"chat_message": self._chat_message})()


@pytest.mark.asyncio
async def test_planner_agent_coerces_tool_summary_to_text(monkeypatch):
    agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
    # Skip real initialization / MCP tools
    agent._delegate = _DummyAssistant(
        ToolCallSummaryMessage(content="ok", source="planner_agent", tool_calls=[], results=[])
    )
    msg = TextMessage(content="hello", source="user")
    out = await agent.handle_message(
        msg,
        MessageContext(
            sender=None,
            topic_id=None,
            is_rpc=False,
            cancellation_token=CancellationToken(),
            message_id="m1",
        ),
    )
    assert type(out) is TextMessage
    assert not isinstance(out, ToolCallSummaryMessage)
    assert out.content == "ok"
