"""Unit-suite isolation from the live task board.

`TaskBoard.from_settings()` builds a board against the real Notion MCP
container as soon as a token is present, and the host now calls it inside every
candidate planning turn whose session asked for anything. On a machine with no
token — this worktree, and CI — that refuses on its own and every test takes
the loud, non-blocking path by accident. On Hugo's machine, with `.env`
loaded, the same tests would reach Notion over the network.

Isolation that holds only when a credential is missing is not isolation, so it
is asserted here instead: every unit test gets a board that refuses, and a test
that means to exercise the real constructor says so with the `real_task_board`
marker.
"""

from __future__ import annotations

import pytest

from fateforger.agents.tasks.board import TaskBoardUnavailable


@pytest.fixture(autouse=True)
def refuse_the_live_task_board(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No unit test reaches Notion, whatever the environment holds."""

    if request.node.get_closest_marker("real_task_board") is not None:
        return

    def _refuse():
        raise TaskBoardUnavailable("the task board is not reachable from a unit test")

    monkeypatch.setattr(
        "fateforger.agents.tasks.board.TaskBoard.from_settings", staticmethod(_refuse)
    )


# --------------------------------------------------------------------------
# The routing harness: one door, driven end to end.
#
# `route_slack_event` is the only way to observe the referent rung, because
# where it sits between the structural resolvers and the session-opening
# branch IS the behaviour under test. This drives the real route with fake
# Slack, a fake runtime and a fake resolver, and records the four things the
# rung's tests ask about: what was opened, where the turn was delivered, what
# the origin was told, and in which order the catalog and the opening happened.
#
# Modelled on the fakes in `tests/unit/test_slack_timeboxing_routing.py`.
# --------------------------------------------------------------------------

from datetime import date, datetime, timedelta  # noqa: E402
from types import SimpleNamespace  # noqa: E402

#: The plan-sessions channel from the 2026-09-05 incident. Its route is the
#: hardcoded "open a new session" door the rung has to get in front of.
PLAN_SESSIONS_CHANNEL = "C0AA6HC1RJL"


class _HarnessClient:
    """Enough Slack to run the route, with every message recorded."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self._ts = 0

    def _next_ts(self) -> str:
        self._ts += 1
        return f"p{self._ts}"

    async def chat_postMessage(self, **payload):
        ts = self._next_ts()
        self.messages.append(
            {
                "channel": payload.get("channel"),
                "ts": ts,
                "text": payload.get("text") or "",
                "blocks": payload.get("blocks"),
                "thread_ts": payload.get("thread_ts"),
            }
        )
        return {"channel": payload.get("channel"), "ts": ts}

    async def chat_update(self, **payload):
        self.messages.append(
            {
                "channel": payload.get("channel"),
                "ts": payload.get("ts"),
                "text": payload.get("text") or "",
                "blocks": payload.get("blocks"),
                "thread_ts": None,
            }
        )
        return {"ok": True}

    async def chat_getPermalink(self, *, channel: str, message_ts: str):
        # The shape Slack returns: the dot is dropped and a `p` prefixed.
        return {
            "permalink": (
                f"https://example.slack.com/archives/{channel}/"
                f"p{message_ts.replace('.', '')}"
            )
        }

    async def conversations_open(self, **_payload):
        return {"channel": {"id": "D_HUGO"}}


class _HarnessRuntime:
    """The adapter bag the route reads, plus a record of every delivery."""

    def __init__(self, *, session_store, resolver) -> None:
        self.timeboxing_session_store = session_store
        self.referent_resolver = resolver
        self.calls: list[tuple[object, object]] = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        from autogen_agentchat.messages import TextMessage

        return SimpleNamespace(chat_message=TextMessage(content="ok", source="bot"))


class _HarnessSessionStore:
    """`load` for the structural resolver, `standing_rows` for the catalog."""

    def __init__(self, *, rows, sessions, event_order) -> None:
        self._rows = rows
        self._sessions = sessions
        self._event_order = event_order
        self.asked: list[str] = []

    async def load(self, session_key: str):
        self.asked.append(session_key)
        return self._sessions.get(session_key)

    async def standing_rows(
        self, *, owner_user_id: str, as_of, open_within, horizon
    ):
        self._event_order.append("catalog")
        return list(self._rows)


class _HarnessPlanning:
    def __init__(self, *, owns: bool) -> None:
        self._owns = owns
        self.ownership_calls: list[tuple[str, str]] = []

    async def owns_thread(self, *, channel_id: str, thread_ts: str) -> bool:
        self.ownership_calls.append((channel_id, thread_ts))
        return self._owns

    async def maybe_handle_thread_reply(
        self, *, channel_id: str, thread_ts: str, text: str, thread_respond
    ):
        from fateforger.slack_bot.planning import ThreadReply, ThreadReplyOutcome

        return ThreadReply(ThreadReplyOutcome.NOT_A_SURFACE)


def _standing_row() -> SimpleNamespace:
    """One committed day, exactly the row the incident's store already held."""

    return SimpleNamespace(
        session_key=f"{PLAN_SESSIONS_CHANNEL}:1788571682.407949",
        status="committed",
        planning_date=date(2026, 9, 5),
        updated_at=datetime(2026, 9, 5, 1, 41),
        revision=7,
        gist=("07:00 Oats", "18:00 Gym", "19:30 Dinner"),
    )


class _RoutingHarness:
    def __init__(self, *, focus, runtime, client, planning, event_order, opened):
        self.focus = focus
        self.runtime = runtime
        self.client = client
        self.planning = planning
        self.event_order = event_order
        self.sessions_opened = opened
        self._origin_ts: str | None = None

    @property
    def delivered_to(self) -> str | None:
        if not self.runtime.calls:
            return None
        return self.runtime.calls[-1][1].key

    @property
    def origin_messages(self) -> list[str]:
        """Everything said in the message the user's own words landed on."""

        return [
            m["text"]
            for m in self.client.messages
            if self._origin_ts is not None and m["ts"] == self._origin_ts
        ]

    async def _route(self, event: dict) -> None:
        from fateforger.slack_bot.handlers import route_slack_event

        before = len(self.client.messages)
        await route_slack_event(
            runtime=self.runtime,
            focus=self.focus,
            default_agent="receptionist_agent",
            event=event,
            bot_user_id=None,
            say=_harness_unused_say,
            client=self.client,
            planning=self.planning,
        )
        posted = self.client.messages[before:]
        if posted and self._origin_ts is None:
            self._origin_ts = posted[0]["ts"]

    async def route_top_level(self, text: str) -> None:
        await self._route(
            {
                "channel": PLAN_SESSIONS_CHANNEL,
                "user": "U_HUGO",
                "text": text,
                "ts": "1788600060.000100",
            }
        )

    async def route_thread_reply(self, text: str) -> None:
        await self._route(
            {
                "channel": PLAN_SESSIONS_CHANNEL,
                "user": "U_HUGO",
                "text": text,
                "thread_ts": "1788599000.000100",
                "ts": "1788600060.000200",
            }
        )


async def _harness_unused_say(**_kwargs):
    return {"channel": PLAN_SESSIONS_CHANNEL, "ts": "unused"}


@pytest.fixture()
def routing_harness(monkeypatch: pytest.MonkeyPatch):
    """Build a harness around the real `route_slack_event`."""

    import fateforger.slack_bot.handlers as handlers_mod
    from fateforger.slack_bot.focus import FocusManager

    def _build(
        *,
        resolver,
        planning_owns_thread: bool = False,
        rows=None,
        sessions=None,
    ) -> _RoutingHarness:
        event_order: list[str] = []
        opened: list[str] = []

        focus = FocusManager(
            ttl_seconds=60,
            allowed_agents=["receptionist_agent", "timeboxing_agent"],
        )
        store = _HarnessSessionStore(
            rows=[_standing_row()] if rows is None else rows,
            sessions=sessions or {},
            event_order=event_order,
        )
        runtime = _HarnessRuntime(session_store=store, resolver=resolver)
        client = _HarnessClient()
        planning = _HarnessPlanning(owns=planning_owns_thread)

        real_open = handlers_mod.open_session_surface

        async def _recording_open(*args, **kwargs):
            event_order.append("open")
            opened.append(kwargs["target_channel"])
            return await real_open(*args, **kwargs)

        async def _fake_turn(**_kwargs):
            from fateforger.slack_bot.messages import SlackBlockMessage

            return SlackBlockMessage(text="turn ran", blocks=[])

        # The route fires `remember` as a background task. With no memory
        # store configured -- a worktree, CI -- it posts its own loud warning
        # into the origin, and whether it wins the race with the route's own
        # first message then depends on the environment rather than on the
        # rung. Isolated here for the same reason the task board is above.
        async def _no_thread_memory(**_kwargs):
            return None

        monkeypatch.setattr(
            "fateforger.slack_bot.thread_memory.remember", _no_thread_memory
        )
        monkeypatch.setattr(handlers_mod, "open_session_surface", _recording_open)
        monkeypatch.setattr(handlers_mod, "_run_adaptive_timebox_turn", _fake_turn)
        monkeypatch.setattr(handlers_mod, "_timebox_backend", lambda: "harness")
        monkeypatch.setattr(
            handlers_mod,
            "_agent_for_channel",
            lambda channel_id: (
                "timeboxing_agent" if channel_id == PLAN_SESSIONS_CHANNEL else None
            ),
        )
        monkeypatch.setattr(
            handlers_mod,
            "_channel_for_agent",
            lambda agent_type: (
                PLAN_SESSIONS_CHANNEL if agent_type == "timeboxing_agent" else None
            ),
        )

        return _RoutingHarness(
            focus=focus,
            runtime=runtime,
            client=client,
            planning=planning,
            event_order=event_order,
            opened=opened,
        )

    return _build
