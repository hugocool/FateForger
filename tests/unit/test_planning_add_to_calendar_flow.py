import asyncio
import json

import pytest

pytest.importorskip("autogen_agentchat")

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from autogen_core import AgentId

from fateforger.agents.schedular.messages import UpsertCalendarEvent, UpsertCalendarEventResult
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import PlanningCoordinator, ThreadReplyOutcome
from fateforger.slack_bot.planning_surface import InterpretedSettledPlanningTurn
from fateforger.slack_bot.surface_intents import SurfaceIntentError, SurfaceIntentInterpreter

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
    def __init__(self, draft: EventDraftPayload | None):
        self._draft = draft
        self.status_updates = []

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

    async def conversations_replies(self, **_kwargs):
        # The coordinator's thread-root fallback; an empty thread resolves to no draft.
        return {"messages": []}


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


def _draft_fixture() -> EventDraftPayload:
    return EventDraftPayload(
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


class _SchemaOutputClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


class _RaisingClient:
    async def create(self, messages, *, json_output):  # noqa: ANN001
        raise RuntimeError("model unavailable")


def _coordinator(store, runtime, client, model_client):
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]
    coordinator._intent_interpreter = SurfaceIntentInterpreter(model_client)  # type: ignore[attr-defined]
    return coordinator


@pytest.mark.asyncio
async def test_thread_reply_update_and_commit_uses_same_add_to_calendar_path(monkeypatch):
    draft = _draft_fixture()  # the existing EventDraftPayload literal from this test, moved to a helper
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(ok=True, calendar_id="primary", event_id="ffplanningxyz", event_url=VALID_EVENT_URL)
    )
    client = _FakeClient()
    coordinator = _coordinator(
        store, runtime, client,
        _SchemaOutputClient({"decision": "update_time_and_add", "selected_time": "17:00"}),
    )

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

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="123.456", text="no, let's do 17:00", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.HANDLED
    assert scheduled
    await asyncio.gather(*scheduled)
    sent, recipient = runtime.calls[-1]
    assert isinstance(sent, UpsertCalendarEvent)
    assert recipient.type == "planner_agent"
    assert sent.start == "2026-01-18T17:00:00"
    assert sent.end == "2026-01-18T17:30:00"
    assert store.status_updates[-1][0] == DraftStatus.SUCCESS


@pytest.mark.asyncio
async def test_okay_is_the_add_press(monkeypatch):
    draft = _draft_fixture()
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(
        UpsertCalendarEventResult(ok=True, calendar_id="primary", event_id="ffplanningxyz", event_url=VALID_EVENT_URL)
    )
    coordinator = _coordinator(
        store, runtime, _FakeClient(),
        _SchemaOutputClient({"decision": "choose_option", "option_id": "add_to_calendar"}),
    )
    scheduled: list[asyncio.Task] = []
    original_create_task = asyncio.create_task
    monkeypatch.setattr(
        "fateforger.slack_bot.planning.asyncio.create_task",
        lambda coro: scheduled.append(original_create_task(coro)) or scheduled[-1],
    )

    async def _thread_respond(*, text: str, blocks=None):
        return None

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="123.456", text="Okay!", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.HANDLED
    await asyncio.gather(*scheduled)
    assert isinstance(runtime.calls[-1][0], UpsertCalendarEvent)
    assert store.status_updates[-1][0] == DraftStatus.SUCCESS


@pytest.mark.asyncio
async def test_a_non_press_reply_returns_the_card_as_context():
    draft = _draft_fixture()
    coordinator = _coordinator(
        _FakeDraftStore(draft), _DummyRuntime(None), _FakeClient(),
        _SchemaOutputClient({"decision": "none"}),
    )

    async def _thread_respond(*, text: str, blocks=None):
        raise AssertionError("a non-press must post nothing itself")

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="123.456", text="why this time?", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.NO_PRESS
    assert "Daily planning session" in (reply.context or "")
    assert "Add to calendar" in (reply.context or "")


@pytest.mark.asyncio
async def test_an_unknown_thread_is_not_a_surface():
    coordinator = _coordinator(
        _FakeDraftStore(None), _DummyRuntime(None), _FakeClient(), _RaisingClient()
    )

    async def _thread_respond(*, text: str, blocks=None):
        raise AssertionError("must not post")

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="999.0", text="Okay!", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.NOT_A_SURFACE


@pytest.mark.asyncio
async def test_owns_thread_answers_from_the_draft_store_without_a_model():
    coordinator = _coordinator(
        _FakeDraftStore(_draft_fixture()), _DummyRuntime(None), _FakeClient(), _RaisingClient()
    )

    assert await coordinator.owns_thread(channel_id="D1", thread_ts="123.456") is True
    assert await coordinator.owns_thread(channel_id="D1", thread_ts="999.0") is False


@pytest.mark.asyncio
async def test_interpreter_failure_on_a_surface_thread_raises():
    coordinator = _coordinator(
        _FakeDraftStore(_draft_fixture()), _DummyRuntime(None), _FakeClient(), _RaisingClient()
    )

    async def _thread_respond(*, text: str, blocks=None):
        return None

    with pytest.raises(SurfaceIntentError) as raised:
        await coordinator.maybe_handle_thread_reply(
            channel_id="D1", thread_ts="123.456", text="Okay!", thread_respond=_thread_respond
        )

    assert isinstance(raised.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_a_schema_violation_reaches_the_caller_as_a_reading_failure():
    # The seam reports one shape, so the caller can tell an unread reply from
    # a press that failed while it was being applied.
    coordinator = _coordinator(
        _FakeDraftStore(_draft_fixture()),
        _DummyRuntime(None),
        _FakeClient(),
        _SchemaOutputClient({"decision": "teleport"}),
    )

    async def _thread_respond(*, text: str, blocks=None):
        return None

    with pytest.raises(SurfaceIntentError):
        await coordinator.maybe_handle_thread_reply(
            channel_id="D1", thread_ts="123.456", text="Okay!", thread_respond=_thread_respond
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,event_url,expected_context_substring",
    [
        (DraftStatus.SUCCESS, VALID_EVENT_URL, "already added"),
        (DraftStatus.PENDING, None, "being added"),
    ],
)
async def test_a_settled_draft_routes_its_reply_with_context_and_touches_nothing(
    status, event_url, expected_context_substring
):
    draft = replace(_draft_fixture(), status=status, event_url=event_url)
    store = _FakeDraftStore(draft)
    runtime = _DummyRuntime(None)
    client = _FakeClient()
    model_client = _SchemaOutputClient({"decision": "none"})
    coordinator = _coordinator(store, runtime, client, model_client)

    async def _thread_respond(*, text: str, blocks=None):
        raise AssertionError("a settled draft must post nothing")

    reply = await coordinator.maybe_handle_thread_reply(
        channel_id="D1", thread_ts="123.456", text="Okay!", thread_respond=_thread_respond
    )

    assert reply.outcome is ThreadReplyOutcome.NO_PRESS
    assert expected_context_substring in (reply.context or "")
    assert runtime.calls == []
    assert store.status_updates == []
    assert client.updates == []
    assert model_client.calls[0][1] is InterpretedSettledPlanningTurn
