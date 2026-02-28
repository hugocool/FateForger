import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId

from fateforger.agents.timeboxing.messages import StartTimeboxing, TimeboxingUserReply
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event


class _FakeResult:
    def __init__(self, chat_message):
        self.chat_message = chat_message


class _FakeTarget:
    def __init__(self, name: str):
        self.name = name


class _FakeHandoffMessage:
    def __init__(self, target_name: str):
        self.target = _FakeTarget(target_name)


class _FakeRuntime:
    def __init__(self, results):
        self.calls = []
        self._results = list(results)

    async def send_message(self, message, recipient: AgentId):
        self.calls.append((message, recipient))
        if self._results:
            return self._results.pop(0)
        return _FakeResult(TextMessage(content="ok", source="bot"))


class _FakeClient:
    def __init__(self):
        self.posted = []
        self.updates = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"channel": payload["channel"], "ts": "p1"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}

async def _unused_say(**_kwargs):
    return {"channel": "C1", "ts": "unused"}


@pytest.mark.asyncio
async def test_routes_root_message_to_timeboxing_start_when_focused():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])
    focus.set_focus("C1:111", "timeboxing_agent", by_user="U1")
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="ok", source="bot"))])
    client = _FakeClient()

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={"channel": "C1", "user": "U1", "text": "plan tomorrow", "ts": "111"},
        bot_user_id=None,
        say=_unused_say,
        client=client,
    )

    assert len(runtime.calls) == 1
    msg, recipient = runtime.calls[0]
    assert isinstance(msg, StartTimeboxing)
    # Root timeboxing sessions are anchored to the bot's prompt message (not the user's message),
    # so the session thread can start cleanly under a deterministic control surface.
    assert msg.thread_ts == "p1"
    assert recipient.type == "timeboxing_agent"
    assert recipient.key == "C1:p1"


@pytest.mark.asyncio
async def test_handoff_from_receptionist_resends_as_timeboxing_start():
    focus = FocusManager(
        ttl_seconds=60, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )
    runtime = _FakeRuntime(
        [
            _FakeResult(_FakeHandoffMessage("timeboxing_agent")),
            _FakeResult(TextMessage(content="ok", source="bot")),
        ]
    )
    client = _FakeClient()

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={"channel": "C1", "user": "U1", "text": "timebox tomorrow", "ts": "222"},
        bot_user_id=None,
        say=_unused_say,
        client=client,
    )

    assert len(runtime.calls) == 2
    first_msg, first_recipient = runtime.calls[0]
    second_msg, second_recipient = runtime.calls[1]

    assert isinstance(first_msg, TextMessage)
    assert first_recipient.type == "receptionist_agent"

    assert isinstance(second_msg, StartTimeboxing)
    assert second_msg.thread_ts == "222"
    assert second_recipient.type == "timeboxing_agent"


@pytest.mark.asyncio
async def test_routes_thread_reply_to_timeboxing_user_reply():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])
    focus.set_focus("C1:root", "timeboxing_agent", by_user="U1")
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="ok", source="bot"))])
    client = _FakeClient()

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "move gym later",
            "thread_ts": "root",
            "ts": "333",
        },
        bot_user_id=None,
        say=_unused_say,
        client=client,
    )

    assert len(runtime.calls) == 1
    msg, _ = runtime.calls[0]
    assert isinstance(msg, TimeboxingUserReply)
    assert msg.thread_ts == "root"
