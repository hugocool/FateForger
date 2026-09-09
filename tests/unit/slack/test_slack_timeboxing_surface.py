"""Where a timeboxing session's messages go: a DM is never redirected, and a
lost focus is recovered rather than dropped.
"""

from __future__ import annotations

import types
import pytest


pytest.importorskip("autogen_agentchat")


# ── a DM is not redirected ────────────────────────────────────────────────────

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
                    target="timeboxing_agent",
                    content="handoff",
                    source="receptionist_agent",
                )
            )
        return TextMessage(content="Timeboxing response", source="timeboxing_agent")




class DummySay:
    def __init__(self, channel_id: str):
        self.calls = []
        self._channel_id = channel_id

    async def __call__(self, **payload):
        self.calls.append(payload)
        return {"channel": self._channel_id, "ts": f"dm_proc_{len(self.calls)}"}


@pytest.mark.asyncio
async def test_timeboxing_handoff_does_not_redirect_from_dm(monkeypatch):
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False)

    runtime = DummyRuntime()
    client = RecordingSlackClient(root_ts="dm_root", reply_ts="dm_proc")
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

    assert [r.type for _, r in runtime.calls] == ["receptionist_agent"]
    # Timeboxing always anchors the session in #timeboxing (even when initiated via DM)
    assert any(p.get("channel") == "C_TIMEBOX" and not p.get("thread_ts") for p in client.posted)


# ── recovering focus ──────────────────────────────────────────────────────────

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import _auto_recover_timeboxing_focus_for_thread


def test_recovers_timeboxing_focus_for_thread_replies(monkeypatch):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_PLAN", raising=False)
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])

    _auto_recover_timeboxing_focus_for_thread(
        focus=focus,
        event={"channel": "C_PLAN", "channel_type": "channel", "ts": "200", "thread_ts": "100"},
        user_id="U1",
    )

    binding = focus.get_focus("C_PLAN:100")
    assert binding is not None
    assert binding.agent_type == "timeboxing_agent"


def test_does_not_recover_focus_for_root_messages(monkeypatch):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_PLAN", raising=False)
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])

    _auto_recover_timeboxing_focus_for_thread(
        focus=focus,
        event={"channel": "C_PLAN", "channel_type": "channel", "ts": "200"},
        user_id="U1",
    )

    assert focus.get_focus("C_PLAN:200") is None
