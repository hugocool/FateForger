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


class _FakeSlackError(Exception):
    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.response = {"ok": False, "error": error_code}


class _FailsFirstUpdateClient(_FakeClient):
    def __init__(self):
        super().__init__()
        self._failed_once = False

    async def chat_update(self, **payload):
        self.updates.append(payload)
        if not self._failed_once:
            self._failed_once = True
            raise _FakeSlackError("msg_too_long")
        return {"ok": True}


class _PlanningReplyHandler:
    def __init__(self):
        self.calls = []

    async def maybe_handle_thread_reply(
        self, *, channel_id: str, thread_ts: str, text: str, thread_respond
    ) -> bool:
        self.calls.append((channel_id, thread_ts, text))
        await thread_respond(text="planning thread handled")
        return True


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


@pytest.mark.asyncio
async def test_route_slack_event_compacts_payload_after_msg_too_long(monkeypatch):
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])
    focus.set_focus("C1:root", "timeboxing_agent", by_user="U1")
    runtime = _FakeRuntime(
        [
            _FakeResult(
                TextMessage(
                    content="X" * 7000,
                    source="bot",
                )
            )
        ]
    )
    client = _FailsFirstUpdateClient()
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.record_error",
        lambda *, component, error_type: errors.append((component, error_type)),
    )

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "reply",
            "thread_ts": "root",
            "ts": "333",
        },
        bot_user_id=None,
        say=_unused_say,
        client=client,
    )

    assert ("slack_routing", "route_exception") in errors
    assert len(client.updates) >= 2
    fallback_update = client.updates[-1]
    assert "Output truncated for Slack delivery" in (fallback_update.get("text") or "")
    assert "blocks" not in fallback_update


@pytest.mark.asyncio
async def test_route_slack_event_records_stage_compute_failure(monkeypatch):
    class _FailingRuntime:
        async def send_message(self, *_args, **_kwargs):
            raise RuntimeError("compute blew up")

    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])
    focus.set_focus("C1:root", "timeboxing_agent", by_user="U1")
    client = _FakeClient()
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.record_error",
        lambda *, component, error_type: errors.append((component, error_type)),
    )

    await route_slack_event(
        runtime=_FailingRuntime(),
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "reply",
            "thread_ts": "root",
            "ts": "444",
        },
        bot_user_id=None,
        say=_unused_say,
        client=client,
    )

    assert ("slack_routing", "stage_compute_failure") in errors
    assert client.updates
    assert "RuntimeError" in (client.updates[-1].get("text") or "")


@pytest.mark.asyncio
async def test_route_slack_event_constraint_refresh_failure_is_non_fatal(monkeypatch):
    class _ExplodingConstraintStore:
        async def list_constraints(self, **_kwargs):
            raise RuntimeError("constraint store unavailable")

    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])
    key = "C1:root"
    focus.set_focus(key, "timeboxing_agent", by_user="U1")
    focus.set_thread_label(
        key,
        title="Timeboxing session",
        request_excerpt=None,
        state="pending",
        by_user="U1",
    )
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="ok", source="bot"))])
    client = _FakeClient()
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.record_error",
        lambda *, component, error_type: errors.append((component, error_type)),
    )

    async def _get_constraint_store():
        return _ExplodingConstraintStore()

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="timeboxing_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "reply",
            "thread_ts": "root",
            "ts": "555",
        },
        bot_user_id=None,
        say=_unused_say,
        client=client,
        get_constraint_store=_get_constraint_store,
    )

    assert client.updates
    assert str(client.updates[-1].get("text") or "").endswith("ok")
    assert ("slack_routing", "constraint_refresh_error") in errors


@pytest.mark.asyncio
async def test_route_slack_event_uses_planning_thread_reply_handler_before_runtime():
    focus = FocusManager(
        ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"]
    )
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="should not run", source="bot"))])
    client = _FakeClient()
    planning = _PlanningReplyHandler()

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "D1",
            "channel_type": "im",
            "user": "U1",
            "text": "yes plan it at 17:00",
            "thread_ts": "root",
            "ts": "777",
        },
        bot_user_id=None,
        say=_unused_say,
        client=client,
        planning=planning,
    )

    assert len(planning.calls) == 1
    assert runtime.calls == []
    assert client.updates
    assert "planning thread handled" in (client.updates[-1].get("text") or "")
