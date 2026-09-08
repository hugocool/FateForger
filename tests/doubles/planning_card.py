"""Doubles for the planning card's add-to-calendar flow."""

from __future__ import annotations

from datetime import datetime, timezone

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload


VALID_EVENT_URL = (
    "https://www.google.com/calendar/event?eid="
    "ZmZwbGFubmluZ3h5eiBodWdvLmV2ZXJzQGV4YW1wbGUuY29t"
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


class _FakeClient:
    def __init__(self):
        self.updates = []

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}

    async def conversations_replies(self, **_kwargs):
        # The coordinator's thread-root fallback; an empty thread resolves to no draft.
        return {"messages": []}
