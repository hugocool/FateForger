"""The revisor's Slack surface: where its messages are routed, and the weekly
review action.
"""

from __future__ import annotations

import types
import pytest


pytest.importorskip("autogen_agentchat")


# ── channel redirect ──────────────────────────────────────────────────────────

from autogen_agentchat.messages import HandoffMessage, TextMessage

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event
from tests.doubles.slack import RecordingSlackClient


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
    client = RecordingSlackClient(root_ts="rev_root", reply_ts="rev_proc", dm_channel="D_DM")
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


# ── the weekly review action ──────────────────────────────────────────────────

from autogen_agentchat.messages import TextMessage

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import (
    FF_APPHOME_WEEKLY_REVIEW_ACTION_ID,
    register_handlers,
)


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[TextMessage, object]] = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        return TextMessage(
            content="*Weekly Review — Phase 1/5 (Reflect)*\nGate: ⏳ pending",
            source="revisor_agent",
        )


class _FakeClient:
    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.updated: list[dict] = []
        self.opened: list[dict] = []
        self.published: list[dict] = []
        self._counter = 0

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        self._counter += 1
        ts = f"{self._counter}.000"
        return {"channel": payload["channel"], "ts": ts}

    async def chat_update(self, **payload):
        self.updated.append(payload)
        return {"ok": True}

    async def conversations_open(self, **payload):
        self.opened.append(payload)
        return {"ok": True, "channel": {"id": "D_DM"}}

    async def chat_getPermalink(self, **payload):
        return {"permalink": "https://example.invalid/thread"}

    async def views_publish(self, **payload):
        self.published.append(payload)
        return {"ok": True}


class _FakeApp:
    def __init__(self, client) -> None:
        self.client = client
        self.actions: dict[str, object] = {}

    def _register(self, bucket: dict[str, object], key: str):
        def decorator(fn):
            bucket[key] = fn
            return fn

        return decorator

    def action(self, action_id: str):
        return self._register(self.actions, action_id)

    def event(self, event_name: str):
        return self._register({}, event_name)

    def command(self, command_name: str):
        return self._register({}, command_name)

    def view(self, callback_id: str):
        return self._register({}, callback_id)


@pytest.mark.asyncio
async def test_apphome_weekly_review_action_starts_revisor_thread(monkeypatch):
    monkeypatch.setattr(settings, "slack_strategy_channel_id", "C_STRATEGY", raising=False)

    runtime = _FakeRuntime()
    focus = FocusManager(
        ttl_seconds=3600,
        allowed_agents=["receptionist_agent", "revisor_agent", "tasks_agent"],
    )
    client = _FakeClient()
    app = _FakeApp(client)
    register_handlers(app=app, runtime=runtime, focus=focus, default_agent="receptionist_agent")

    handler = app.actions[FF_APPHOME_WEEKLY_REVIEW_ACTION_ID]
    ack_calls: list[bool] = []

    async def _ack():
        ack_calls.append(True)

    await handler(
        ack=_ack,
        body={"user": {"id": "U1"}},
        client=client,
        logger=types.SimpleNamespace(info=lambda *a, **k: None),
    )

    assert ack_calls == [True]
    assert runtime.calls
    sent_message, recipient = runtime.calls[0]
    assert sent_message.content == "Start a weekly review."
    assert recipient.type == "revisor_agent"
    assert recipient.key.startswith("C_STRATEGY:")

    root_posts = [p for p in client.posted if p["channel"] == "C_STRATEGY" and not p.get("thread_ts")]
    assert root_posts
    processing_posts = [
        p for p in client.posted if p["channel"] == "C_STRATEGY" and p.get("thread_ts")
    ]
    assert processing_posts
    assert client.updated
    assert "Phase 1/5 (Reflect)" in client.updated[-1].get("text", "")
