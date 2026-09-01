"""One path builds every timeboxing session surface: root header + threaded card.

Regression suite for the 2026-08-31 22:57 incident (see the spec of the same
date): the slash fresh start used one Slack message as ack, progress card,
outcome card and session-thread root, and a root relabel erased the card.
Assertions are over identifiers this system minted, never over user prose.
"""

from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.slack_bot import handlers
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import route_slack_event
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    build_timebox_date_card,
)


class _FakeRuntime:
    """route_slack_event demands one; the adaptive turn is stubbed past it."""

    def __init__(self) -> None:
        self.calls: list = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        raise AssertionError(
            "runtime.send_message must not be reached on the harness path"
        )


class _FakeClient:
    """Mints sequential ts values so root and card are tellable apart."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.updates: list[dict] = []
        self._counter = 0

    def _mint_ts(self) -> str:
        self._counter += 1
        return f"100.{self._counter:06d}"

    async def chat_postMessage(self, **payload):
        record = dict(payload)
        record["ts"] = self._mint_ts()
        self.posted.append(record)
        return {"channel": payload["channel"], "ts": record["ts"]}

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


def _action_ids(blocks: list[dict] | None) -> set[str]:
    return {
        element["action_id"]
        for block in blocks or []
        if block.get("type") == "actions"
        for element in block.get("elements", [])
        if "action_id" in element
    }


def _writes_to(client: _FakeClient, ts: str) -> list[dict]:
    """Every content the message with this ts ever displayed, in order."""
    born = [dict(p) for p in client.posted if p["ts"] == ts]
    edits = [u for u in client.updates if u.get("ts") == ts]
    return born + edits


@pytest.fixture(autouse=True)
def _harness_backend(monkeypatch):
    """This suite exercises the harness surface specifically.

    tests/conftest.py pins every test to `FF_TIMEBOX_BACKEND=legacy` by
    default so route_slack_event never shells out unasked; the session
    surface under test only exists on the harness path, so reaching it here
    is deliberate, same as test_slack_timeboxing_channel_redirect.py and
    test_harness_approval_action.py already do for the same reason.
    """
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")


@pytest.fixture
def focus() -> FocusManager:
    return FocusManager(
        ttl_seconds=60,
        allowed_agents=["receptionist_agent", "timeboxing_agent"],
    )


@pytest.fixture
def stub_turn(monkeypatch):
    """Replace the kernel turn with a real date card for tomorrow.

    The card comes from the production builder so the metadata the relabel
    decodes (date, tz, thread_ts) is the real contract, not a lookalike.
    """
    calls: list[dict] = []

    async def fake_turn(**kwargs):
        calls.append(kwargs)
        return build_timebox_date_card(
            session_key=kwargs["session_key"],
            expected_revision=1,
            user_id=kwargs["actor_user_id"],
            channel_id=kwargs["card_channel"],
            thread_ts=kwargs["card_thread_ts"],
            planned_date="2026-09-02",
            tz_name="Europe/Amsterdam",
        )

    monkeypatch.setattr(handlers, "_run_adaptive_timebox_turn", fake_turn)
    return calls


@pytest.fixture
def same_channel_session(monkeypatch):
    """Anchor the session in the origin channel (the /timebox-in-#plan-sessions case)."""
    monkeypatch.setattr(handlers, "_channel_for_agent", lambda _agent: None)


async def _noop_say(**_kwargs):
    return None


def _slash_event(channel: str = "C1") -> dict:
    """The synthetic event _route_command_as_message builds for /timebox."""
    return {
        "type": "message",
        "text": "",
        "user": "U1",
        "channel": channel,
        "ts": "1788300000.000001",
        "channel_type": "channel",
    }


async def test_a_fresh_slash_start_builds_root_plus_threaded_card(
    focus, stub_turn, same_channel_session
):
    """Exactly two messages: a root that is only ever a header, and a card
    threaded under it whose final state still carries Confirm."""
    client = _FakeClient()

    await route_slack_event(
        runtime=_FakeRuntime(),
        focus=focus,
        default_agent="timeboxing_agent",
        event=_slash_event(),
        bot_user_id=None,
        say=_noop_say,
        client=client,
    )

    assert len(client.posted) == 2, (
        "a fresh slash start is exactly a root and a threaded working message"
    )
    root, card = client.posted
    assert card["ts"] != root["ts"]
    assert card.get("thread_ts") == root["ts"], "the card must live in the root's thread"

    final_card = _writes_to(client, card["ts"])[-1]
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(final_card.get("blocks"))

    for state in _writes_to(client, root["ts"]):
        assert FF_TIMEBOX_COMMIT_START_ACTION_ID not in _action_ids(
            state.get("blocks")
        ), "the root is a header; card controls must never land on it"

    expected_label = handlers.format_relative_day_label(
        planned_date="2026-09-02", tz_name="Europe/Amsterdam"
    )
    final_root = _writes_to(client, root["ts"])[-1]
    assert final_root["text"] == handlers._timeboxing_thread_root_text(
        title=f"Timeboxing session for {expected_label}",
        request_excerpt=None,
        state="pending",
    )

    assert stub_turn, "the kernel turn ran"
    assert stub_turn[0]["session_key"] == f"C1:{root['ts']}"
    assert stub_turn[0]["card_thread_ts"] == root["ts"]


class _FakeHandoffTarget:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeHandoffMessage:
    def __init__(self, target_name: str) -> None:
        self.target = _FakeHandoffTarget(target_name)


class _HandoffRuntime:
    """First turn: receptionist answers with a handoff. No second turn --
    the adaptive stub owns the session turn."""

    def __init__(self) -> None:
        self.calls: list = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))

        class _R:
            chat_message = _FakeHandoffMessage("timeboxing_agent")

        return _R()


class _CrossChannelClient(_FakeClient):
    async def conversations_open(self, **payload):
        return {"channel": {"id": "D1"}}

    async def chat_getPermalink(self, **payload):
        return {"permalink": "https://slack.example/p/1"}


async def test_a_cross_channel_handoff_builds_the_same_surface_and_still_dms(
    focus, stub_turn, monkeypatch
):
    """User speaks in C-general, receptionist hands off, the session anchors
    in the dedicated channel: root + threaded card there, and a DM copy."""
    monkeypatch.setattr(handlers, "_channel_for_agent", lambda _agent: "C-timebox")
    client = _CrossChannelClient()

    await route_slack_event(
        runtime=_HandoffRuntime(),
        focus=focus,
        default_agent="receptionist_agent",
        event={
            "channel": "C-general",
            "user": "U1",
            "text": "timebox tomorrow",
            "ts": "333",
            "channel_type": "channel",
        },
        bot_user_id=None,
        say=_noop_say,
        client=client,
    )

    in_session_channel = [
        p for p in client.posted if p["channel"] == "C-timebox"
    ]
    assert len(in_session_channel) == 2, "root and threaded card in the session channel"
    root, card = in_session_channel
    assert card.get("thread_ts") == root["ts"]
    final_card = _writes_to(client, card["ts"])[-1]
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(final_card.get("blocks"))
    for state in _writes_to(client, root["ts"]):
        assert FF_TIMEBOX_COMMIT_START_ACTION_ID not in _action_ids(
            state.get("blocks")
        )

    dm_posts = [p for p in client.posted if p["channel"] == "D1"]
    assert len(dm_posts) == 1, "a cross-channel start still DMs the card"
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(dm_posts[0].get("blocks"))

    assert stub_turn and stub_turn[0]["session_key"] == f"C-timebox:{root['ts']}"
