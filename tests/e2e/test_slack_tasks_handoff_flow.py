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
                chat_message=DummyHandoff(DummyTarget("tasks_agent"))
            )
        return TextMessage(content="Added 2 items to Weekend Prep.", source="tasks_agent")


class DummySay:
    def __init__(self):
        self.calls = []

    async def __call__(self, **payload):
        self.calls.append(payload)
        return {"channel": "C1", "ts": f"p{len(self.calls)}"}


class DummyClient:
    def __init__(self):
        self.posted = []
        self.updates = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"channel": payload["channel"], "ts": "p1"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}


@pytest.mark.asyncio
async def test_slack_handoff_sets_focus_and_forwards_to_tasks_agent():
    runtime = DummyRuntime()
    focus = FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "tasks_agent"]
    )
    say = DummySay()
    client = DummyClient()

    event = {"channel": "C1", "user": "U1", "text": "create list weekend prep", "ts": "1"}
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=say,
        client=client,
    )

    assert runtime.calls == ["receptionist_agent", "tasks_agent"]
    assert any("tasks_agent" in (u.get("text") or "") for u in client.updates)

    key = FocusManager.thread_key("C1", None, "1")
    binding = focus.get_focus(key)
    assert binding is not None
    assert binding.agent_type == "tasks_agent"
