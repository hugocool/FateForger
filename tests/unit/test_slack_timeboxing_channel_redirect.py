import types

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import HandoffMessage, TextMessage

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event
from fateforger.slack_bot.messages import SlackThreadStateMessage
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
)


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
        # Stage-0 commit prompt (blocks include action IDs)
        return SlackBlockMessage(
            text="Confirm planned date: 2026-01-18 (Europe/Amsterdam)",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "Pick a day"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": FF_TIMEBOX_COMMIT_START_ACTION_ID,
                            "text": {"type": "plain_text", "text": "Start timeboxing"},
                            "value": "channel_id=C_TIMEBOX&thread_ts=tb_root&user_id=U1&date=2026-01-18&tz=Europe%2FAmsterdam",
                        },
                        {
                            "type": "button",
                            "action_id": FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID,
                            "text": {"type": "plain_text", "text": "Pick another day"},
                            "value": "channel_id=C_TIMEBOX&thread_ts=tb_root&user_id=U1&date=2026-01-18&tz=Europe%2FAmsterdam",
                        },
                    ],
                },
            ],
        )


class DoneRuntime:
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
        return SlackThreadStateMessage(text="Finalized.", thread_state="done")


class DummyClient:
    def __init__(self):
        self.posted = []
        self.updates = []
        self.opened = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        channel = payload["channel"]
        # root vs thread reply
        if payload.get("thread_ts"):
            return {"channel": channel, "ts": "tb_proc"}
        return {"channel": channel, "ts": "tb_root"}

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
async def test_timeboxing_handoff_redirects_into_configured_channel(monkeypatch):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False)

    runtime = DummyRuntime()
    client = DummyClient()
    say = DummySay()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    event = {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}
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
    assert runtime.calls[1][1].key == "C_TIMEBOX:tb_root"
    # Thread root + processing reply in the timeboxing channel
    assert any(p["channel"] == "C_TIMEBOX" and not p.get("thread_ts") for p in client.posted)
    assert any(p["channel"] == "C_TIMEBOX" and p.get("thread_ts") == "tb_root" for p in client.posted)
    # User gets a DM with the commit prompt (best-effort), including a deep link button.
    assert client.opened and client.opened[0]["users"] == ["U1"]
    assert any(
        p["channel"] == "D_DM"
        and FF_TIMEBOX_COMMIT_START_ACTION_ID in str(p.get("blocks"))
        and "Go to Thread" in str(p.get("blocks"))
        for p in client.posted
    )

    # Origin thread gets redirected notice (not the timebox itself)
    assert any(
        u["channel"] == "C_ORIG" and "Continuing in <#C_TIMEBOX>" in u.get("text", "")
        for u in client.updates
    )
    assert any(
        u.get("blocks")
        and "Go to Thread" in str(u.get("blocks"))
        and "url" in str(u.get("blocks"))
        for u in client.updates
    )


@pytest.mark.asyncio
async def test_timeboxing_reply_in_origin_thread_is_forwarded(monkeypatch):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False)

    runtime = DummyRuntime()
    client = DummyClient()
    say = DummySay()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    # First message creates redirect + focus
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={"channel": "C_ORIG", "user": "U1", "text": "timebox", "ts": "1"},
        bot_user_id=None,
        say=say,
        client=client,
    )

    # Reply in the original thread should be forwarded to the timeboxing thread
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C_ORIG",
            "user": "U1",
            "text": "move gym later",
            "thread_ts": "1",
            "ts": "2",
        },
        bot_user_id=None,
        say=say,
        client=client,
    )

    # The last runtime call is a timeboxing_agent call keyed to the timeboxing thread
    assert runtime.calls[-1][1].type == "timeboxing_agent"
    assert runtime.calls[-1][1].key == "C_TIMEBOX:tb_root"


@pytest.mark.asyncio
async def test_timeboxing_done_updates_thread_header_emoji(monkeypatch):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False)

    runtime = DoneRuntime()
    client = DummyClient()
    say = DummySay()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )

    event = {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=say,
        client=client,
    )

    assert any(
        u.get("channel") == "C_TIMEBOX"
        and u.get("ts") == "tb_root"
        and ":white_check_mark:" in (u.get("text") or "")
        for u in client.updates
    )
