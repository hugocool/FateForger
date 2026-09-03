from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId

from fateforger.agents.timeboxing.messages import StartTimeboxing, TimeboxingUserReply
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import _with_agent_attribution, route_slack_event
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.planning import ThreadReply, ThreadReplyOutcome


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
    def __init__(self, reply: ThreadReply | Exception, owns: bool = False):
        self.calls = []
        self.ownership_calls = []
        self._reply = reply
        self._owns = owns

    async def owns_thread(self, *, channel_id: str, thread_ts: str) -> bool:
        self.ownership_calls.append((channel_id, thread_ts))
        return self._owns

    async def maybe_handle_thread_reply(
        self, *, channel_id: str, thread_ts: str, text: str, thread_respond
    ) -> ThreadReply:
        self.calls.append((channel_id, thread_ts, text))
        if isinstance(self._reply, Exception):
            raise self._reply
        if self._reply.outcome is ThreadReplyOutcome.HANDLED:
            await thread_respond(text="planning thread handled")
        return self._reply


class _SessionStore:
    def __init__(self, keys: dict[str, object]):
        self._keys = keys
        self.asked: list[str] = []

    async def load(self, session_key: str):
        self.asked.append(session_key)
        return self._keys.get(session_key)


def _dm_reply_event(text: str = "Okay!") -> dict:
    return {
        "channel": "D1",
        "channel_type": "im",
        "user": "U1",
        "text": text,
        "thread_ts": "root",
        "ts": "777",
    }


async def _unused_say(**_kwargs):
    return {"channel": "C1", "ts": "unused"}


async def _route(*, runtime, focus, client, planning, event):
    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=_unused_say,
        client=client,
        planning=planning,
    )


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
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="should not run", source="bot"))])
    client = _FakeClient()
    planning = _PlanningReplyHandler(ThreadReply(ThreadReplyOutcome.HANDLED))

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("yes plan it at 17:00"))

    assert len(planning.calls) == 1
    assert runtime.calls == []
    assert "planning thread handled" in (client.updates[-1].get("text") or "")


@pytest.mark.asyncio
async def test_a_non_press_reply_routes_with_the_card_described():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="answer", source="bot"))])
    client = _FakeClient()
    planning = _PlanningReplyHandler(
        ThreadReply(ThreadReplyOutcome.NO_PRESS, context="The user is replying under a planning card titled X.")
    )

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("why 10:38?"))

    assert len(runtime.calls) == 1
    sent = runtime.calls[0][0]
    assert sent.content.startswith("The user is replying under a planning card titled X.")
    assert sent.content.rstrip().endswith("why 10:38?")


@pytest.mark.asyncio
async def test_an_interpreter_failure_is_reported_and_never_routed():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="should not run", source="bot"))])
    client = _FakeClient()
    planning = _PlanningReplyHandler(RuntimeError("model unavailable"))

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event())

    assert runtime.calls == []
    assert "planning card" in (client.updates[-1].get("text") or "").lower()


@pytest.mark.asyncio
async def test_a_dm_timeboxing_thread_is_found_in_the_store_after_focus_is_gone(monkeypatch):
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "timeboxing_agent"])
    runtime = _FakeRuntime([])
    runtime.timeboxing_session_store = _SessionStore({"D1:dm": SimpleNamespace(status="open")})
    client = _FakeClient()
    planning = _PlanningReplyHandler(ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE), owns=False)
    ran: list[dict] = []

    async def _fake_turn(**kwargs):
        ran.append(kwargs)
        return SlackBlockMessage(text="turn ran", blocks=[])

    monkeypatch.setattr("fateforger.slack_bot.handlers._run_adaptive_timebox_turn", _fake_turn)
    monkeypatch.setattr("fateforger.slack_bot.handlers._timebox_backend", lambda: "harness")

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("move gym to 19:00"))

    assert runtime.timeboxing_session_store.asked == ["D1:dm"]
    assert ran and ran[0]["session_key"] == "D1:dm"
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_a_planning_thread_in_a_dm_is_never_claimed_by_a_live_session():
    # The DM session key is thread-blind: `D1:dm` names the whole DM, so a
    # live session would otherwise claim the planning card's own thread and
    # pin focus on it. Resolvers are ordered, and planning comes first.
    focus = FocusManager(
        ttl_seconds=60, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="answer", source="bot"))])
    runtime.timeboxing_session_store = _SessionStore(
        {"D1:dm": SimpleNamespace(status="open")}
    )
    client = _FakeClient()
    planning = _PlanningReplyHandler(
        ThreadReply(ThreadReplyOutcome.NO_PRESS, context="CARD CONTEXT"), owns=True
    )

    await _route(
        runtime=runtime,
        focus=focus,
        client=client,
        planning=planning,
        event=_dm_reply_event("why 10:38?"),
    )

    assert planning.ownership_calls == [("D1", "root")]
    assert runtime.timeboxing_session_store.asked == []
    assert focus.get_focus("D1:root") is None
    assert len(runtime.calls) == 1
    assert runtime.calls[0][1].type == "receptionist_agent"
    assert "CARD CONTEXT" in runtime.calls[0][0].content


@pytest.mark.asyncio
async def test_a_thread_that_is_no_surface_routes_as_before():
    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent"])
    runtime = _FakeRuntime([_FakeResult(TextMessage(content="hello", source="bot"))])
    runtime.timeboxing_session_store = _SessionStore({})
    client = _FakeClient()
    planning = _PlanningReplyHandler(ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE))

    await _route(runtime=runtime, focus=focus, client=client, planning=planning, event=_dm_reply_event("hi"))

    assert len(runtime.calls) == 1
    assert runtime.calls[0][0].content == "hi"
    assert runtime.calls[0][1].type == "receptionist_agent"


@pytest.mark.asyncio
async def test_a_handoff_root_quotes_the_users_words_not_the_card_context(monkeypatch):
    # NO_PRESS prefixes `cleaned_text` with the card's own description so the
    # *agent* doesn't answer cold. That prefix must never leak into a message
    # a human reads as if it were the user's own words -- confirmed site: the
    # handoff "Incoming request" root posted to the target channel.
    import fateforger.slack_bot.handlers as handlers_mod

    focus = FocusManager(ttl_seconds=60, allowed_agents=["receptionist_agent", "planner_agent"])
    runtime = _FakeRuntime(
        [
            _FakeResult(_FakeHandoffMessage("planner_agent")),
            _FakeResult(TextMessage(content="ok", source="bot")),
        ]
    )
    client = _FakeClient()
    planning = _PlanningReplyHandler(
        ThreadReply(ThreadReplyOutcome.NO_PRESS, context="CARD CONTEXT")
    )
    monkeypatch.setattr(
        handlers_mod,
        "_channel_for_agent",
        lambda agent_type: "C2" if agent_type == "planner_agent" else None,
    )

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C1",
            "user": "U1",
            "text": "why 10:38?",
            "thread_ts": "root",
            "ts": "999",
        },
        bot_user_id=None,
        say=_unused_say,
        client=client,
        planning=planning,
    )

    # The agent-bound sends carry the card context.
    assert len(runtime.calls) == 2
    assert "CARD CONTEXT" in runtime.calls[0][0].content
    assert "CARD CONTEXT" in runtime.calls[1][0].content

    # The human-visible handoff root quotes the raw reply, never the context.
    handoff_roots = [
        p for p in client.posted if "Incoming request" in str(p.get("text") or "")
    ]
    assert len(handoff_roots) == 1
    root_text = handoff_roots[0]["text"]
    assert "why 10:38?" in root_text
    assert "CARD CONTEXT" not in root_text


def test_a_text_reply_is_mrkdwn_without_an_agent_label():
    payload = _with_agent_attribution({"text": "**Focus Session**: pick one task."}, "admonisher_agent")

    assert payload == {"text": "*Focus Session*: pick one task."}


def test_a_block_reply_keeps_its_context_footer():
    payload = _with_agent_attribution({"text": "t", "blocks": [{"type": "section"}]}, "planner_agent")

    assert payload["blocks"][-1]["type"] == "context"
