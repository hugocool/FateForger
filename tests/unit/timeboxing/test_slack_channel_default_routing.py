import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event
from tests.doubles.slack import RecordingSlackClient


class DummyRuntime:
    def __init__(self):
        self.calls = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        return TextMessage(content="ok", source=recipient.type)




async def _unused_say(**_kwargs):
    return {"channel": "C1", "ts": "unused"}


@pytest.mark.asyncio
async def test_the_specialist_channel_opens_a_session_directly(monkeypatch):
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_PLAN", raising=False)
    runtime = DummyRuntime()
    client = RecordingSlackClient()
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

    assert runtime.calls == []
    assert any(p.get("channel") == "C_PLAN" and not p.get("thread_ts") for p in client.posted)
