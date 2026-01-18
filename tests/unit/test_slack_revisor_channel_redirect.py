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
                    target="revisor_agent",
                    content="handoff",
                    source="receptionist_agent",
                )
            )
        return TextMessage(content="Revisor response", source="revisor_agent")


class DummyClient:
    def __init__(self):
        self.posted = []
        self.updates = []
        self.opened = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        channel = payload["channel"]
        if payload.get("thread_ts"):
            return {"channel": channel, "ts": "rev_proc"}
        return {"channel": channel, "ts": "rev_root"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}

    async def chat_getPermalink(self, **payload):
        return {"permalink": "https://example.invalid/permalink"}

    async def conversations_open(self, **payload):
        self.opened.append(payload)
        return {"ok": True, "channel": {"id": "D_DM"}}


class DummySay:
    def __init__(self):
        self.calls = []

    async def __call__(self, **payload):
        self.calls.append(payload)
        return {"channel": "C_ORIG", "ts": f"orig_proc_{len(self.calls)}"}


@pytest.mark.asyncio
async def test_revisor_handoff_redirects_into_configured_strategy_channel(monkeypatch):
    monkeypatch.setattr(settings, "slack_strategy_channel_id", "C_STRATEGY", raising=False)

    runtime = DummyRuntime()
    client = DummyClient()
    say = DummySay()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "revisor_agent"]
    )

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={"channel": "C_ORIG", "user": "U1", "text": "plan my week", "ts": "1"},
        bot_user_id=None,
        say=say,
        client=client,
    )

    assert [r.type for _, r in runtime.calls] == ["receptionist_agent", "revisor_agent"]
    assert runtime.calls[1][1].key == "C_STRATEGY:rev_root"
    assert any(p["channel"] == "C_STRATEGY" for p in client.posted)
    revisor_root = next(p for p in client.posted if p["channel"] == "C_STRATEGY" and not p.get("thread_ts"))
    assert revisor_root.get("username") == "Reviewer"
    assert client.opened and client.opened[0]["users"] == ["U1"]
    assert any(p["channel"] == "D_DM" and "Go to Thread" in str(p.get("blocks")) for p in client.posted)
    assert any(
        u.get("blocks")
        and "Go to Thread" in str(u.get("blocks"))
        and "url" in str(u.get("blocks"))
        for u in client.updates
    )
