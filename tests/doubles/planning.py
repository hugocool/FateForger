"""Doubles for the planning-reminder dispatch path."""

from __future__ import annotations

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload


class RecordingDraftStore:
    """Enough of the draft store for a card to be built and posted."""

    def __init__(self):
        self.created = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return EventDraftPayload(
            draft_id=kwargs["draft_id"],
            user_id=kwargs["user_id"],
            channel_id=kwargs["channel_id"],
            message_ts=None,
            calendar_id=kwargs["calendar_id"],
            event_id=kwargs["event_id"],
            title=kwargs["title"],
            description=kwargs["description"],
            timezone=kwargs["timezone"],
            start_at_utc=kwargs["start_at_utc"],
            duration_min=kwargs["duration_min"],
            status=DraftStatus.PENDING,
            event_url=None,
            last_error=None,
        )

    async def attach_message(self, **kwargs):  # noqa: ARG002
        return None


class NoSlotRuntime:
    """A runtime whose planner never answers, so the dispatcher uses defaults."""

    event_draft_store = None
    planning_guardian = None
    planning_reconciler = None
    timeboxing_session_store = None

    def __init__(self, *, ledger):
        self.event_draft_store = RecordingDraftStore()
        self.timeboxing_session_store = ledger

    async def send_message(self, *args, **kwargs):  # noqa: ARG002
        return None
