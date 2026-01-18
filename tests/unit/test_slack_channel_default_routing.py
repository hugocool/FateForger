import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event
from fateforger.agents.timeboxing.messages import StartTimeboxing


class DummyRuntime:
    def __init__(self):
        self.calls = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        return TextMessage(content="ok", source=recipient.type)


class DummyClient:
    def __init__(self):
        self.posted = []
        self.updates = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        # simulate Slack response message timestamp
        return {"ok": True, "channel": payload["channel"], "ts": "m1"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}


async def _unused_say(**_kwargs):
    return {"channel": "C1", "ts": "unused"}


@pytest.mark.asyncio
async def test_specialist_channel_routes_directly_to_timeboxing_agent(monkeypatch):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_PLAN", raising=False)
    runtime = DummyRuntime()
    client = DummyClient()
    focus = FocusManager(ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"])

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={"channel": "C_PLAN", "user": "U1", "text": "lets plan tomorrow", "ts": "1"},
        bot_user_id=None,
        say=_unused_say,
        client=client,
    )

    assert len(runtime.calls) == 1
    msg, recipient = runtime.calls[0]
    assert recipient.type == "timeboxing_agent"
    assert isinstance(msg, StartTimeboxing)

