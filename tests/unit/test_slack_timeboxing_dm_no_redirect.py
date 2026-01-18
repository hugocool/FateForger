import types

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import HandoffMessage, TextMessage

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event


class DummyRuntime:
    def __init__(self):
        self.calls = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        if recipient.type == "receptionist_agent":
            return types.SimpleNamespace(
                chat_message=HandoffMessage(
                    target="timeboxing_agent",
                    content="handoff",
                    source="receptionist_agent",
                )
            )
        return TextMessage(content="Timeboxing response", source="timeboxing_agent")


class DummyClient:
    def __init__(self):
        self.posted = []
        self.updates = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        channel = payload["channel"]
        if payload.get("thread_ts"):
            return {"channel": channel, "ts": "dm_proc"}
        return {"channel": channel, "ts": "dm_root"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}


class DummySay:
    def __init__(self, channel_id: str):
        self.calls = []
        self._channel_id = channel_id

    async def __call__(self, **payload):
        self.calls.append(payload)
        return {"channel": self._channel_id, "ts": f"dm_proc_{len(self.calls)}"}


@pytest.mark.asyncio
async def test_timeboxing_handoff_does_not_redirect_from_dm(monkeypatch):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False)

    runtime = DummyRuntime()
    client = DummyClient()
    say = DummySay("D_DM")
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    event = {
        "channel": "D_DM",
        "channel_type": "im",
        "user": "U1",
        "text": "timebox tomorrow",
        "ts": "1",
    }
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=say,
        client=client,
    )

    assert [r.type for _, r in runtime.calls] == ["receptionist_agent", "timeboxing_agent"]
    # Timeboxing always anchors the session in #timeboxing (even when initiated via DM)
    assert runtime.calls[1][1].key == "C_TIMEBOX:dm_root"
    assert any(p.get("channel") == "C_TIMEBOX" and not p.get("thread_ts") for p in client.posted)
    assert any(p.get("channel") == "C_TIMEBOX" and p.get("thread_ts") == "dm_root" for p in client.posted)
