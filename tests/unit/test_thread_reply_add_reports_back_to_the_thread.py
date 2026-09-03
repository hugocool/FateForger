"""A calendar add started from a thread reply must answer in that thread.

Seen live on 2026-09-03. Hugo replied "okay!" under the Admonisher's planning
card; the bot answered "Adding this planning session to your calendar…" in the
thread, the upsert succeeded six seconds later (`ok=True`, same event id), and
the *card* at the top of the thread was edited in place to "✅ Added to
calendar". The thread itself never heard another word. From where Hugo was
reading, the last thing the bot said was "Adding…", and he had to ask whether
it had worked.

Both halves were correct on their own terms -- the card is the source of truth
and it was updated -- and the composite was a silent success, which is the same
shape as a receipt that says `committed: true` about an in-memory dict.

`_add_to_calendar_async` reports only through the `respond` it is handed, and
the thread path hands it the card responder. So it gains an optional `notify`
for the thread, and the thread-reply path supplies it. The button path passes
nothing and is unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fateforger.agents.schedular.messages import UpsertCalendarEventResult
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import PlanningCoordinator

from .test_planning_add_to_calendar_flow import (
    VALID_EVENT_URL,
    _DummyRuntime,
    _FakeClient,
    _FakeDraftStore,
    _FakePlanningSessionStore,
)


def _draft(status: DraftStatus = DraftStatus.PENDING) -> EventDraftPayload:
    return EventDraftPayload(
        draft_id="draft_thread_1",
        user_id="U1",
        channel_id="D1",
        message_ts="100.200",
        calendar_id="primary",
        event_id="ffplanningthread",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=status,
        event_url=None,
        last_error=None,
    )


def _coordinator(store, result) -> PlanningCoordinator:
    coordinator = PlanningCoordinator(
        runtime=_DummyRuntime(result), focus=object(), client=_FakeClient()
    )  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]
    coordinator._planning_session_store = _FakePlanningSessionStore()  # type: ignore[attr-defined]
    return coordinator


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs) -> None:
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_success_is_reported_to_the_thread_as_well_as_the_card() -> None:
    store = _FakeDraftStore(_draft())
    coordinator = _coordinator(
        store,
        UpsertCalendarEventResult(
            ok=True, calendar_id="primary", event_id="ffplanningthread",
            event_url=VALID_EVENT_URL,
        ),
    )
    card, thread = _Recorder(), _Recorder()

    await coordinator._add_to_calendar_async(
        draft_id="draft_thread_1", respond=card, notify=thread
    )

    assert card.calls, "the card is still the source of truth and must still update"
    assert len(thread.calls) == 1, "exactly one answer in the thread"
    assert VALID_EVENT_URL in thread.calls[0]["text"]
    assert store.status_updates[-1][0] is DraftStatus.SUCCESS


@pytest.mark.asyncio
async def test_failure_is_reported_to_the_thread_with_the_reason() -> None:
    """A silent failure in a thread is worse than a silent success: the user
    walks away believing the day is booked."""

    store = _FakeDraftStore(_draft())
    coordinator = _coordinator(
        store,
        UpsertCalendarEventResult(
            ok=False, calendar_id="primary", event_id="", event_url=None,
            error="calendar said no",
        ),
    )
    card, thread = _Recorder(), _Recorder()

    await coordinator._add_to_calendar_async(
        draft_id="draft_thread_1", respond=card, notify=thread
    )

    assert len(thread.calls) == 1
    assert "calendar said no" in thread.calls[0]["text"]
    assert store.status_updates[-1][0] is DraftStatus.FAILURE


@pytest.mark.asyncio
async def test_the_button_path_passes_no_notify_and_is_unchanged() -> None:
    """Pressing "Add to calendar" on the card has no thread to answer in."""

    store = _FakeDraftStore(_draft())
    coordinator = _coordinator(
        store,
        UpsertCalendarEventResult(
            ok=True, calendar_id="primary", event_id="ffplanningthread",
            event_url=VALID_EVENT_URL,
        ),
    )
    card = _Recorder()

    await coordinator._add_to_calendar_async(draft_id="draft_thread_1", respond=card)

    assert card.calls
    assert store.status_updates[-1][0] is DraftStatus.SUCCESS


@pytest.mark.asyncio
async def test_the_thread_reply_path_hands_its_own_responder_through(monkeypatch) -> None:
    """The wiring, end to end from a reply: the responder that posted
    "Adding…" is the one that must post the outcome."""

    store = _FakeDraftStore(_draft(DraftStatus.DRAFT))
    coordinator = _coordinator(
        store,
        UpsertCalendarEventResult(
            ok=True, calendar_id="primary", event_id="ffplanningthread",
            event_url=VALID_EVENT_URL,
        ),
    )

    async def decided(*, text, draft):
        return SimpleNamespace(should_handle=True, commit=True, selected_time=None)

    monkeypatch.setattr(coordinator, "_interpret_planning_thread_reply", decided)

    seen: dict = {}

    async def recording_add(*, draft_id, respond, notify=None):
        seen["draft_id"] = draft_id
        seen["notify"] = notify

    monkeypatch.setattr(coordinator, "_add_to_calendar_async", recording_add)

    thread = _Recorder()
    handled = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="100.200", text="okay!", thread_respond=thread
    )
    await asyncio.sleep(0)  # let the scheduled add task run

    assert handled is True
    assert seen.get("draft_id") == "draft_thread_1"
    assert seen.get("notify") is thread, "the thread responder must reach the add"
    assert any("Adding" in c["text"] for c in thread.calls)
