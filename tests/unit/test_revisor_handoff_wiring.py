from __future__ import annotations

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat.agents")

from autogen_agentchat.messages import HandoffMessage, TextMessage
from autogen_core import CancellationToken, DefaultTopicId, MessageContext

from fateforger.agents.revisor import agent as revisor_agent_module


def test_revisor_passes_allowed_handoffs_to_assistant(monkeypatch):
    captured: dict[str, object] = {}

    class DummyAssistantAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(revisor_agent_module, "AssistantAgent", DummyAssistantAgent)
    monkeypatch.setattr(
        revisor_agent_module, "build_autogen_chat_client", lambda _name: object()
    )

    handoffs = ["tasks_agent_handoff"]
    revisor_agent_module.RevisorAgent("revisor_agent", allowed_handoffs=handoffs)

    assert captured.get("handoffs") == handoffs


@pytest.mark.asyncio
async def test_revisor_handle_text_returns_handoff_message():
    class _FakeAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            class _Resp:
                def __init__(self):
                    self.chat_message = HandoffMessage(
                        target="tasks_agent",
                        content="handoff",
                        source="revisor_agent",
                    )

            return _Resp()

    agent = revisor_agent_module.RevisorAgent("revisor_agent", allowed_handoffs=[])
    agent._assistant = _FakeAssistant()
    ctx = MessageContext(
        sender=None,
        topic_id=DefaultTopicId(),
        is_rpc=False,
        cancellation_token=CancellationToken(),
        message_id="m1",
    )

    result = await agent.handle_text(TextMessage(content="refine sprint", source="U1"), ctx)

    assert isinstance(result, HandoffMessage)
    assert result.target == "tasks_agent"
