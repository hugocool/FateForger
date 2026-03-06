import asyncio

import pytest

pytest.importorskip("autogen_agentchat")

from datetime import datetime, timezone
from types import SimpleNamespace

from autogen_core import AgentId

from fateforger.agents.schedular.messages import UpsertCalendarEvent, UpsertCalendarEventResult
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import PlanningCoordinator, PlanningThreadReplyIntent

VALID_EVENT_URL = (
    "https://www.google.com/calendar/event?eid="
    "ZmZwbGFubmluZ3h5eiBodWdvLmV2ZXJzQGV4YW1wbGUuY29t"
)
SHORT_DOMAIN_EVENT_URL = (
    "https://www.google.com/calendar/event?eid="
    "ZmZwbGFubmluZ3h5eiBodWdvLmV2ZXJzQG0"
)
MALFORMED_EID_EVENT_URL = (
    "https://www.google.com/calendar/event?eid="
    "ZmZwbGFubmluZ3h5eg"
)


class _FakeDraftStore:
    def __init__(self, draft: EventDraftPayload):
        self._draft = draft
        self.status_updates = []

    async def get_by_message(self, *, channel_id: str, message_ts: str):
        if channel_id != self._draft.channel_id or message_ts != self._draft.message_ts:
            return None
        return self._draft

    async def get_by_draft_id(self, *, draft_id: str):
        if draft_id != self._draft.draft_id:
            return None
        return self._draft

    async def update_time(
        self,
        *,
        channel_id: str,
        message_ts: str,
        start_at_utc: str | None = None,
        duration_min: int | None = None,
    ):
        if channel_id != self._draft.channel_id or message_ts != self._draft.message_ts:
            return None
        updates = dict(self._draft.__dict__)
        if start_at_utc is not None:
            updates["start_at_utc"] = start_at_utc
        if duration_min is not None:
            updates["duration_min"] = duration_min
        self._draft = self._draft.__class__(**updates)
        return self._draft

    async def update_status(self, *, draft_id: str, status: DraftStatus, event_url=None, last_error=None):
        assert draft_id == self._draft.draft_id
        self.status_updates.append((status, event_url, last_error))
        self._draft = self._draft.__class__(**{**self._draft.__dict__, "status": status, "event_url": event_url, "last_error": last_error})
        return self._draft


class _DummyRuntime:
    def __init__(self, result):
        self.calls = []
        self._result = result

    async def send_message(self, message, recipient: AgentId):
        self.calls.append((message, recipient))
        return self._result


class _FakePlanningSessionStore:
    def __init__(self):
        self.upserts = []

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        return kwargs


class _FakeAnchorStore:
    def __init__(self):
        self.upserts = []

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        return kwargs


class _FakeClient:
    def __init__(self):
        self.updates = []

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}


@pytest.mark.asyncio
async def test_add_to_calendar_success_updates_status_and_returns_url_button():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.PENDING,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz",
            event_url=VALID_EVENT_URL,
        )
    )

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]
    planning_session_store = _FakePlanningSessionStore()
    coordinator._planning_session_store = planning_session_store  # type: ignore[attr-defined]

    updates = []

    async def respond(*, text, blocks, replace_original):
        updates.append({"text": text, "blocks": blocks, "replace_original": replace_original})

    await coordinator._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)

    assert runtime.calls
    sent, recipient = runtime.calls[-1]
    assert isinstance(sent, UpsertCalendarEvent)
    assert recipient.type == "planner_agent"
    assert sent.start == "2026-01-18T10:00:00"
    assert sent.end == "2026-01-18T10:30:00"

    assert store.status_updates
    assert store.status_updates[-1][0] == DraftStatus.SUCCESS
    assert updates
    assert any(
        el.get("url") == VALID_EVENT_URL
        for block in updates[-1]["blocks"]
        if block.get("type") == "actions"
        for el in block.get("elements", [])
    )
    assert planning_session_store.upserts
    assert planning_session_store.upserts[-1]["event_id"] == "ffplanningxyz"
    assert planning_session_store.upserts[-1]["event_url"] == VALID_EVENT_URL


@pytest.mark.asyncio
async def test_add_to_calendar_failure_sets_failure_status():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.PENDING,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(ok=False, calendar_id="primary", event_id="ffplanningxyz", error="auth expired")
    )

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    updates = []

    async def respond(*, text, blocks, replace_original):
        updates.append({"text": text, "blocks": blocks, "replace_original": replace_original})

    await coordinator._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)

    assert store.status_updates
    assert store.status_updates[-1][0] == DraftStatus.FAILURE
    assert updates


@pytest.mark.asyncio
async def test_add_to_calendar_ok_without_url_treated_as_failure():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.PENDING,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz",
            event_url=None,
        )
    )

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    updates = []

    async def respond(*, text, blocks, replace_original):
        updates.append({"text": text, "blocks": blocks, "replace_original": replace_original})

    await coordinator._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)

    assert store.status_updates
    status, _event_url, last_error = store.status_updates[-1]
    assert status == DraftStatus.FAILURE
    assert "no event url" in (last_error or "").lower()
    assert updates


@pytest.mark.asyncio
async def test_add_to_calendar_ok_with_short_domain_google_eid_treated_as_success():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.PENDING,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz",
            event_url=SHORT_DOMAIN_EVENT_URL,
        )
    )

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    updates = []

    async def respond(*, text, blocks, replace_original):
        updates.append({"text": text, "blocks": blocks, "replace_original": replace_original})

    await coordinator._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)

    assert store.status_updates
    status, _event_url, last_error = store.status_updates[-1]
    assert status == DraftStatus.SUCCESS
    assert last_error is None
    assert _event_url == SHORT_DOMAIN_EVENT_URL
    assert updates


@pytest.mark.asyncio
async def test_add_to_calendar_ok_with_malformed_google_eid_treated_as_failure():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.PENDING,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz",
            event_url=MALFORMED_EID_EVENT_URL,
        )
    )

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    updates = []

    async def respond(*, text, blocks, replace_original):
        updates.append({"text": text, "blocks": blocks, "replace_original": replace_original})

    await coordinator._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)

    assert store.status_updates
    status, _event_url, last_error = store.status_updates[-1]
    assert status == DraftStatus.FAILURE
    assert "incomplete event url token" in (last_error or "").lower()
    assert updates


@pytest.mark.asyncio
async def test_add_to_calendar_success_updates_anchor_when_event_id_changes():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.PENDING,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz-20260306",
            event_url=VALID_EVENT_URL,
        )
    )

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]
    anchor_store = _FakeAnchorStore()
    coordinator._anchor_store = anchor_store  # type: ignore[attr-defined]

    updates = []

    async def respond(*, text, blocks, replace_original):
        updates.append({"text": text, "blocks": blocks, "replace_original": replace_original})

    await coordinator._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)

    assert anchor_store.upserts
    assert anchor_store.upserts[-1]["event_id"] == "ffplanningxyz-20260306"
    assert updates


@pytest.mark.asyncio
async def test_thread_reply_update_and_commit_uses_same_add_to_calendar_path(monkeypatch):
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.DRAFT,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz",
            event_url=VALID_EVENT_URL,
        )
    )
    client = _FakeClient()

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    async def _fake_interpret(*, text: str, draft: EventDraftPayload):
        assert text == "yes plan it at 17:00"
        assert draft.draft_id == "draft_abc123"
        return PlanningThreadReplyIntent(
            should_handle=True, commit=True, selected_time="17:00"
        )

    coordinator._interpret_planning_thread_reply = _fake_interpret  # type: ignore[method-assign]

    scheduled: list[asyncio.Task] = []
    original_create_task = asyncio.create_task

    def _capture_task(coro):
        task = original_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr("fateforger.slack_bot.planning.asyncio.create_task", _capture_task)

    thread_updates = []

    async def _thread_respond(*, text: str, blocks=None):
        thread_updates.append({"text": text, "blocks": blocks})

    handled = await coordinator.maybe_handle_thread_reply(
        channel_id="D1",
        thread_ts="123.456",
        text="yes plan it at 17:00",
        thread_respond=_thread_respond,
    )

    assert handled is True
    assert scheduled, "expected add-to-calendar async task to be scheduled"
    await asyncio.gather(*scheduled)

    assert runtime.calls
    sent, recipient = runtime.calls[-1]
    assert isinstance(sent, UpsertCalendarEvent)
    assert recipient.type == "planner_agent"
    assert sent.start == "2026-01-18T17:00:00"
    assert sent.end == "2026-01-18T17:30:00"
    assert store.status_updates[-1][0] == DraftStatus.SUCCESS
    assert client.updates, "expected card updates via chat_update"
    assert thread_updates


@pytest.mark.asyncio
async def test_interpret_thread_reply_requires_structured_output():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.DRAFT,
        event_url=None,
        last_error=None,
    )
    coordinator = PlanningCoordinator(runtime=object(), focus=object(), client=object())  # type: ignore[arg-type]

    class _StructuredInterpreter:
        async def on_messages(self, _messages, _cancellation):
            return SimpleNamespace(
                chat_message=SimpleNamespace(
                    content={
                        "action": "update_time_and_add_to_calendar",
                        "selected_time": "17:00",
                    }
                )
            )

    coordinator._ensure_thread_reply_interpreter = lambda: _StructuredInterpreter()  # type: ignore[method-assign]

    parsed = await coordinator._interpret_planning_thread_reply(
        text="yes plan it at 17:00", draft=draft
    )

    assert parsed.should_handle is True
    assert parsed.commit is True
    assert parsed.selected_time == "17:00"


@pytest.mark.asyncio
async def test_thread_reply_has_no_text_heuristic_fallback_when_interpreter_fails():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.DRAFT,
        event_url=None,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz",
            event_url=VALID_EVENT_URL,
        )
    )
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    class _BrokenInterpreter:
        async def on_messages(self, _messages, _cancellation):
            return SimpleNamespace(chat_message=SimpleNamespace(content={"foo": "bar"}))

    coordinator._ensure_thread_reply_interpreter = lambda: _BrokenInterpreter()  # type: ignore[method-assign]

    handled = await coordinator.maybe_handle_thread_reply(
        channel_id="D1",
        thread_ts="123.456",
        text="yes plan it at 17:00",
        thread_respond=lambda **_kwargs: None,
    )

    assert handled is False
    assert runtime.calls == []
    assert store.status_updates == []


@pytest.mark.asyncio
async def test_thread_reply_commit_on_success_draft_returns_terminal_noop_message():
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.SUCCESS,
        event_url=VALID_EVENT_URL,
        last_error=None,
    )
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(
            ok=True,
            calendar_id="primary",
            event_id="ffplanningxyz",
            event_url=VALID_EVENT_URL,
        )
    )
    client = _FakeClient()

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    async def _fake_interpret(*, text: str, draft: EventDraftPayload):
        assert text == "yes plan it at 17:00"
        return PlanningThreadReplyIntent(
            should_handle=True, commit=True, selected_time=None
        )

    coordinator._interpret_planning_thread_reply = _fake_interpret  # type: ignore[method-assign]

    thread_updates = []

    async def _thread_respond(*, text: str, blocks=None):
        thread_updates.append({"text": text, "blocks": blocks})

    handled = await coordinator.maybe_handle_thread_reply(
        channel_id="D1",
        thread_ts="123.456",
        text="yes plan it at 17:00",
        thread_respond=_thread_respond,
    )

    assert handled is True
    assert runtime.calls == []
    assert client.updates
    assert thread_updates
    assert "already on your calendar" in thread_updates[-1]["text"].lower()
