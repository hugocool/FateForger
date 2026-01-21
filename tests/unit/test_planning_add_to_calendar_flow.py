import pytest

pytest.importorskip("autogen_agentchat")

from datetime import datetime, timezone

from autogen_core import AgentId

from fateforger.agents.schedular.messages import UpsertCalendarEvent, UpsertCalendarEventResult
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import PlanningCoordinator


class _FakeDraftStore:
    def __init__(self, draft: EventDraftPayload):
        self._draft = draft
        self.status_updates = []

    async def get_by_draft_id(self, *, draft_id: str):
        if draft_id != self._draft.draft_id:
            return None
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
        UpsertCalendarEventResult(ok=True, calendar_id="primary", event_id="ffplanningxyz", event_url="https://example.invalid")
    )

    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=object())  # type: ignore[arg-type]
    coordinator._draft_store = store  # type: ignore[attr-defined]
    coordinator._guardian = None  # type: ignore[attr-defined]

    updates = []

    async def respond(*, text, blocks, replace_original):
        updates.append({"text": text, "blocks": blocks, "replace_original": replace_original})

    await coordinator._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)

    assert runtime.calls
    sent, recipient = runtime.calls[-1]
    assert isinstance(sent, UpsertCalendarEvent)
    assert recipient.type == "planner_agent"

    assert store.status_updates
    assert store.status_updates[-1][0] == DraftStatus.SUCCESS
    assert updates
    assert any(
        el.get("url") == "https://example.invalid"
        for block in updates[-1]["blocks"]
        if block.get("type") == "actions"
        for el in block.get("elements", [])
    )


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

