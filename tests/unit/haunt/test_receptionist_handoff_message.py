import pytest

pytest.importorskip("autogen_agentchat")

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from autogen_agentchat.base import Handoff
from autogen_agentchat.messages import HandoffMessage, TextMessage
from autogen_core import CancellationToken, DefaultTopicId, MessageContext

from fateforger.agents.receptionist.agent import ReceptionistAgent
from fateforger.haunt.orchestrator import HauntOrchestrator


class _FakeAssistant:
    def __init__(self, chat_message):
        self._chat_message = chat_message

    async def on_messages(self, _messages, _cancellation_token):
        class _Resp:
            def __init__(self, chat_message):
                self.chat_message = chat_message

        return _Resp(self._chat_message)


@pytest.mark.asyncio
async def test_receptionist_logs_handoff_target_when_target_is_string():
    scheduler = AsyncIOScheduler()
    haunt = HauntOrchestrator(scheduler)
    agent = ReceptionistAgent(
        "receptionist_agent",
        allowed_handoffs=[Handoff(target="timeboxing_agent")],
        haunt=haunt,
    )
    agent._assistant = _FakeAssistant(
        HandoffMessage(
            target="timeboxing_agent",
            content="handoff",
            source="receptionist_agent",
        )
    )

    captured = []

    async def _capture(envelope):
        captured.append(envelope)
        return None

    agent._haunt.record_envelope = _capture

    ctx = MessageContext(
        sender=None,
        topic_id=DefaultTopicId(),
        is_rpc=False,
        cancellation_token=CancellationToken(),
        message_id="m1",
    )

    result = await agent.handle_text(
        TextMessage(content="lets timebox friday", source="U1"),
        ctx,
    )

    assert isinstance(result, HandoffMessage)
    assert result.target == "timeboxing_agent"

    # inbound + outbound
    assert len(captured) == 2
    assert captured[1].content == "handoff:timeboxing_agent"
