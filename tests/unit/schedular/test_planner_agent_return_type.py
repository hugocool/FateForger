import logging

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


def _ctx():
    return MessageContext(
        sender=None,
        topic_id=None,
        is_rpc=False,
        cancellation_token=CancellationToken(),
        message_id="m1",
    )


async def _reply(chat_message):
    agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
    # Skip real initialization / MCP tools
    agent._delegate = _DummyAssistant(chat_message)
    return await agent.handle_message(TextMessage(content="hello", source="user"), _ctx())


@pytest.mark.asyncio
async def test_planner_agent_returns_model_prose_unchanged():
    out = await _reply(TextMessage(content="You have one event on Sunday.", source="planner_agent"))
    assert type(out) is TextMessage
    assert out.content == "You have one event on Sunday."


@pytest.mark.asyncio
async def test_planner_agent_withholds_tool_summary_instead_of_stringifying_it(caplog):
    """#180: a ToolCallSummaryMessage is the raw MCP payload, not an answer.

    The old behaviour wrapped it in a TextMessage, which made the calendar payload
    indistinguishable from prose to every layer downstream — including the Slack boundary,
    which then posted it. Withholding it is the only option that stays honest.
    """
    leak = (
        '[{"type": "text", "text": "[{\\"htmlLink\\":\\"https://www.google.com/calendar/event'
        '?eid=abc\\",\\"creator\\":{\\"email\\":\\"hugo.evers@gmail.com\\"}}]", '
        '"annotations": null}]'
    )
    with caplog.at_level(logging.ERROR):
        out = await _reply(
            ToolCallSummaryMessage(
                content=leak, source="planner_agent", tool_calls=[], results=[]
            )
        )

    assert type(out) is TextMessage
    assert "hugo.evers@gmail.com" not in out.content
    assert "google.com/calendar/event" not in out.content
    assert out.content.strip()
    assert any(
        record.levelno >= logging.ERROR and "ToolCallSummaryMessage" in record.getMessage()
        for record in caplog.records
    ), "withholding tool output must log an error"


@pytest.mark.asyncio
async def test_planner_delegate_reflects_on_tool_use():
    """The agent must summarise list-events rather than return it (#180).

    Asserted on the delegate's construction because that flag is *why* the reply is prose;
    a test on the reply alone would pass against a stub that never called a tool.
    """
    import fateforger.agents.schedular.agent as agent_module

    captured: dict = {}

    class _Recorder:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    original_assistant = agent_module.AssistantAgent
    original_tools = agent_module.get_calendar_mcp_tools
    original_timeout = agent_module.with_timeout
    original_client = agent_module.build_autogen_chat_client

    async def _no_tools(*args, **kwargs):
        return []

    async def _passthrough(_label, awaitable, **kwargs):
        return await awaitable

    agent_module.AssistantAgent = _Recorder
    agent_module.get_calendar_mcp_tools = _no_tools
    agent_module.with_timeout = _passthrough
    agent_module.build_autogen_chat_client = lambda *a, **k: object()
    try:
        agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
        await agent._ensure_initialized()
    finally:
        agent_module.AssistantAgent = original_assistant
        agent_module.get_calendar_mcp_tools = original_tools
        agent_module.with_timeout = original_timeout
        agent_module.build_autogen_chat_client = original_client

    assert captured["reflect_on_tool_use"] is True
