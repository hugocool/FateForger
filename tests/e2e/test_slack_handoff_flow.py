import types

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat.messages")

from autogen_agentchat.messages import TextMessage

from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event


class DummyTarget:
    def __init__(self, name: str):
        self.name = name


class DummyHandoff:
    def __init__(self, target: DummyTarget):
        self.target = target


class DummyRuntime:
    def __init__(self):
        self.calls = []

    async def send_message(self, message, recipient):
        self.calls.append(recipient.type)
        if recipient.type == "receptionist_agent":
            return types.SimpleNamespace(
                chat_message=DummyHandoff(DummyTarget("planner_agent"))
            )
        return TextMessage(content="Planner response", source="planner")


class DummySay:
    def __init__(self):
        self.calls = []

    async def __call__(self, **payload):
        self.calls.append(payload)


@pytest.mark.asyncio
async def test_slack_handoff_sets_focus_and_forwards():
    runtime = DummyRuntime()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "planner_agent"]
    )
    say = DummySay()

    event = {"channel": "C1", "user": "U1", "text": "hello", "ts": "1"}
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=say,
    )

    assert runtime.calls == ["receptionist_agent", "planner_agent"]
    assert say.calls[0]["text"] == "Planner response"

    key = FocusManager.thread_key("C1", None, "1")
    binding = focus.get_focus(key)
    assert binding is not None
    assert binding.agent_type == "planner_agent"

    event_followup = {
        "channel": "C1",
        "user": "U1",
        "text": "next",
        "thread_ts": "1",
        "ts": "2",
    }
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event_followup,
        bot_user_id=None,
        say=say,
    )

    assert runtime.calls[-1] == "planner_agent"
