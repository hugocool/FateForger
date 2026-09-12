"""A planning card's controls must act on the draft the card names.

Live on 2026-09-11 a card proposed 17:29, Hugo picked 18:00, and the calendar
got 17:29. The Admonisher posts every planning card twice with identical
blocks — once to the DM, once as a copy in #admonishments — and the two
controls disagreed about how they found the draft: the pickers looked it up by
the clicked message's (channel_id, message_ts), while Add looked it up by the
draft_id carried in its own button value. Every draft row is keyed to the DM,
so a pick on the copy resolved to nothing and returned silently, and Add then
booked the untouched proposal.

Two properties are asserted here:
  - every control resolves its draft by the id the card carries, wherever the
    clicked message lives;
  - Add books what the card currently shows, taking the picker's value out of
    the click payload's `state` when the pick and the press arrive together.

Assertions are over identifiers this system minted (draft ids, action ids,
block ids) and over times, never over card prose.
"""

from __future__ import annotations

import asyncio
import json
import types
from datetime import datetime, timezone

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.schedular.messages import (
    UpsertCalendarEvent,
    UpsertCalendarEventResult,
)
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import register_handlers
from fateforger.slack_bot.planning import (
    FF_EVENT_ADD_ACTION_ID,
    FF_EVENT_BLOCK_PICK_TIME,
    FF_EVENT_START_TIME_ACTION_ID,
    _card_payload,
)

VALID_EVENT_URL = (
    "https://www.google.com/calendar/event?eid="
    "ZmZwbGFubmluZ3h5eiBodWdvLmV2ZXJzQGV4YW1wbGUuY29t"
)

# The DM copy: the only place an event_drafts row is ever keyed to.
DM_CHANNEL = "D1"
DM_TS = "1757604540.000100"
# The #admonishments copy: same blocks, same draft_id, different coordinates.
LOG_CHANNEL = "C0ADMONLOG"
LOG_TS = "1757604541.000200"

# 15:29Z is 17:29 in Europe/Amsterdam on 2026-09-11 (CEST, UTC+2).
PROPOSED_UTC = datetime(2026, 9, 11, 15, 29, tzinfo=timezone.utc)


def _draft(**overrides) -> EventDraftPayload:
    base = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id=DM_CHANNEL,
        message_ts=DM_TS,
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=PROPOSED_UTC.isoformat(),
        duration_min=30,
        status=DraftStatus.DRAFT,
        event_url=None,
        last_error=None,
    )
    return base.__class__(**{**base.__dict__, **overrides})


class _FakeDraftStore:
    """A store keyed the way the real one is: one row, living in the DM."""

    def __init__(self, draft: EventDraftPayload | None) -> None:
        self._draft = draft
        self.status_updates: list[tuple] = []

    async def get_by_message(self, *, channel_id: str, message_ts: str):
        if self._draft is None:
            return None
        if channel_id != self._draft.channel_id or message_ts != self._draft.message_ts:
            return None
        return self._draft

    async def get_by_draft_id(self, *, draft_id: str):
        if self._draft is None or draft_id != self._draft.draft_id:
            return None
        return self._draft

    def _apply(self, start_at_utc, duration_min):
        updates = dict(self._draft.__dict__)
        if start_at_utc is not None:
            updates["start_at_utc"] = start_at_utc
        if duration_min is not None:
            updates["duration_min"] = duration_min
        self._draft = self._draft.__class__(**updates)
        return self._draft

    async def update_time(
        self,
        *,
        channel_id: str,
        message_ts: str,
        start_at_utc: str | None = None,
        duration_min: int | None = None,
    ):
        if self._draft is None:
            return None
        if channel_id != self._draft.channel_id or message_ts != self._draft.message_ts:
            return None
        return self._apply(start_at_utc, duration_min)

    async def update_time_by_draft_id(
        self,
        *,
        draft_id: str,
        start_at_utc: str | None = None,
        duration_min: int | None = None,
    ):
        if self._draft is None or draft_id != self._draft.draft_id:
            return None
        return self._apply(start_at_utc, duration_min)

    async def update_status(
        self, *, draft_id: str, status: DraftStatus, event_url=None, last_error=None
    ):
        if self._draft is None or draft_id != self._draft.draft_id:
            return None
        self.status_updates.append((status, event_url, last_error))
        self._draft = self._draft.__class__(
            **{
                **self._draft.__dict__,
                "status": status,
                "event_url": event_url,
                "last_error": last_error,
            }
        )
        return self._draft


class _FakeRuntime:
    """Carries the stores the coordinator reads off the runtime."""

    def __init__(self, store: _FakeDraftStore) -> None:
        self.event_draft_store = store
        self.calls: list[tuple] = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        if isinstance(message, UpsertCalendarEvent):
            return UpsertCalendarEventResult(
                ok=True,
                calendar_id="primary",
                event_id="ffplanningxyz",
                event_url=VALID_EVENT_URL,
            )
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.updated: list[dict] = []

    async def chat_update(self, **payload):
        self.updated.append(payload)
        return {"ok": True}

    async def chat_postMessage(self, **payload):
        return {"ok": True}


class _FakeApp:
    def __init__(self, client) -> None:
        self.client = client
        self.actions: dict[str, object] = {}
        self.views: dict[str, object] = {}

    def _register(self, bucket: dict[str, object], key: str):
        def decorator(fn):
            bucket[key] = fn
            return fn

        return decorator

    def action(self, action_id: str):
        return self._register(self.actions, action_id)

    def event(self, event_name: str):
        return self._register({}, event_name)

    def command(self, command_name: str):
        return self._register({}, command_name)

    def view(self, callback_id: str):
        return self._register(self.views, callback_id)


def _register(store: _FakeDraftStore):
    client = _FakeClient()
    app = _FakeApp(client)
    runtime = _FakeRuntime(store)
    register_handlers(
        app=app,
        runtime=runtime,
        focus=FocusManager(ttl_seconds=3600, allowed_agents=["planner_agent"]),
        default_agent="planner_agent",
    )
    return app, runtime


async def _ack():
    return None


_LOGGER = types.SimpleNamespace(
    info=lambda *a, **k: None,
    warning=lambda *a, **k: None,
    debug=lambda *a, **k: None,
    exception=lambda *a, **k: None,
)


def _responder():
    calls: list[dict] = []

    async def respond(*, text=None, blocks=None, replace_original=None):
        calls.append({"text": text, "blocks": blocks})

    return respond, calls


def _pick_body(*, channel_id: str, message_ts: str, selected_time: str, blocks):
    return {
        "user": {"id": "U1"},
        "channel": {"id": channel_id},
        "message": {"ts": message_ts, "blocks": blocks},
        "actions": [
            {
                "action_id": FF_EVENT_START_TIME_ACTION_ID,
                "selected_time": selected_time,
            }
        ],
    }


def _add_body(*, channel_id: str, message_ts: str, blocks, state=None):
    body = {
        "user": {"id": "U1"},
        "channel": {"id": channel_id},
        "message": {"ts": message_ts, "blocks": blocks},
        "actions": [
            {
                "action_id": FF_EVENT_ADD_ACTION_ID,
                "value": json.dumps({"draft_id": "draft_abc123"}),
            }
        ],
    }
    if state is not None:
        body["state"] = state
    return body


def _state_with_time(selected_time: str) -> dict:
    return {
        "values": {
            FF_EVENT_BLOCK_PICK_TIME: {
                FF_EVENT_START_TIME_ACTION_ID: {
                    "type": "timepicker",
                    "selected_time": selected_time,
                }
            }
        }
    }


def _local_hhmm(draft: EventDraftPayload) -> str:
    from zoneinfo import ZoneInfo

    from dateutil import parser as date_parser

    return (
        date_parser.isoparse(draft.start_at_utc)
        .astimezone(ZoneInfo(draft.timezone))
        .strftime("%H:%M")
    )


async def test_a_pick_on_the_admonishments_copy_moves_the_draft():
    """The copy's coordinates match no row; the draft_id on the card does."""
    store = _FakeDraftStore(_draft())
    blocks = _card_payload(store._draft)["blocks"]
    app, _runtime = _register(store)
    respond, _calls = _responder()

    await app.actions[FF_EVENT_START_TIME_ACTION_ID](
        ack=_ack,
        body=_pick_body(
            channel_id=LOG_CHANNEL,
            message_ts=LOG_TS,
            selected_time="18:00",
            blocks=blocks,
        ),
        respond=respond,
        logger=_LOGGER,
    )

    assert _local_hhmm(store._draft) == "18:00"


async def test_a_pick_on_the_dm_card_still_moves_the_draft():
    """Regression: the message that does match must keep working."""
    store = _FakeDraftStore(_draft())
    blocks = _card_payload(store._draft)["blocks"]
    app, _runtime = _register(store)
    respond, calls = _responder()

    await app.actions[FF_EVENT_START_TIME_ACTION_ID](
        ack=_ack,
        body=_pick_body(
            channel_id=DM_CHANNEL,
            message_ts=DM_TS,
            selected_time="18:00",
            blocks=blocks,
        ),
        respond=respond,
        logger=_LOGGER,
    )

    assert _local_hhmm(store._draft) == "18:00"
    assert calls, "the clicked card was never redrawn"


async def test_add_books_the_time_the_card_shows_not_the_stored_proposal(monkeypatch):
    """The pick and the press arrive a second apart; the press carries the pick."""
    store = _FakeDraftStore(_draft())
    blocks = _card_payload(store._draft)["blocks"]
    app, runtime = _register(store)
    respond, _calls = _responder()

    scheduled: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def _capture(coro):
        task = original_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr("fateforger.slack_bot.planning.asyncio.create_task", _capture)

    await app.actions[FF_EVENT_ADD_ACTION_ID](
        ack=_ack,
        body=_add_body(
            channel_id=LOG_CHANNEL,
            message_ts=LOG_TS,
            blocks=blocks,
            state=_state_with_time("18:00"),
        ),
        respond=respond,
        logger=_LOGGER,
    )
    await asyncio.gather(*scheduled)

    upserts = [m for m, _r in runtime.calls if isinstance(m, UpsertCalendarEvent)]
    assert upserts, "nothing was booked"
    assert upserts[-1].start == "2026-09-11T18:00:00"
    assert upserts[-1].end == "2026-09-11T18:30:00"


async def test_a_pick_on_a_card_without_a_draft_id_button_changes_nothing(monkeypatch):
    """A card that names no draft must fail loudly in the log, not corrupt a row."""
    store = _FakeDraftStore(_draft())
    stripped = [
        b
        for b in _card_payload(store._draft)["blocks"]
        if b.get("type") != "actions"
    ]
    app, _runtime = _register(store)
    respond, _calls = _responder()

    await app.actions[FF_EVENT_START_TIME_ACTION_ID](
        ack=_ack,
        body=_pick_body(
            channel_id=LOG_CHANNEL,
            message_ts=LOG_TS,
            selected_time="18:00",
            blocks=stripped,
        ),
        respond=respond,
        logger=_LOGGER,
    )

    assert _local_hhmm(store._draft) == "17:29"


async def test_a_card_without_a_draft_id_button_falls_back_to_coordinates():
    """The old lookup stays: a card that names no draft is not a broken card."""
    store = _FakeDraftStore(_draft())
    stripped = [
        b
        for b in _card_payload(store._draft)["blocks"]
        if b.get("type") != "actions"
    ]
    app, _runtime = _register(store)
    respond, _calls = _responder()

    await app.actions[FF_EVENT_START_TIME_ACTION_ID](
        ack=_ack,
        body=_pick_body(
            channel_id=DM_CHANNEL,
            message_ts=DM_TS,
            selected_time="18:00",
            blocks=stripped,
        ),
        respond=respond,
        logger=_LOGGER,
    )

    assert _local_hhmm(store._draft) == "18:00"


async def test_add_without_state_books_the_stored_draft(monkeypatch):
    """No `state` in the payload is not a reason to fail or to guess."""
    store = _FakeDraftStore(_draft())
    blocks = _card_payload(store._draft)["blocks"]
    app, runtime = _register(store)
    respond, _calls = _responder()

    scheduled: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def _capture(coro):
        task = original_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr("fateforger.slack_bot.planning.asyncio.create_task", _capture)

    await app.actions[FF_EVENT_ADD_ACTION_ID](
        ack=_ack,
        body=_add_body(channel_id=DM_CHANNEL, message_ts=DM_TS, blocks=blocks),
        respond=respond,
        logger=_LOGGER,
    )
    await asyncio.gather(*scheduled)

    upserts = [m for m, _r in runtime.calls if isinstance(m, UpsertCalendarEvent)]
    assert upserts, "nothing was booked"
    assert upserts[-1].start == "2026-09-11T17:29:00"
