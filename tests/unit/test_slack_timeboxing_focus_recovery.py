import pytest

pytest.importorskip("autogen_agentchat")

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

