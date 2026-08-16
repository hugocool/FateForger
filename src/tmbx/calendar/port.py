"""Calendar port and snapshot tokens.

A snapshot pins the etag of every observed event. Writes check it first.
The legacy engine writes against a snapshot with no precondition, so an edit
made elsewhere mid-session is silently overwritten; undo replays its
before-state just as blindly and can destroy a newer edit. Both are closed
here.

``drift()`` reports every difference between a snapshot and the live
calendar — an etag change, a vanished event, or an appeared event — as one
flat set of affected event ids. It does not try to judge whether an
appeared event actually conflicts with the write being attempted: ``drift``
only sees ``Snapshot`` (calendar_id, day, etags) and the live events, never
the blocks a caller is about to commit, so it has no basis for that
judgement. Scoping "does this drift matter to *my* write" belongs to the
caller, which knows which event ids its write touches and can intersect
that set against ``drift()``'s output. Reporting everything here keeps the
primitive honest; filtering by relevance here would make it guess.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date as date_type
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class CalendarEvent(BaseModel):
    """One calendar event, provider-neutral.

    ``uid``, ``handle`` and ``slug`` live in provider extended properties.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    summary: str
    description: str = ""
    start: datetime
    end: datetime
    etag: str = ""
    uid: str | None = None
    handle: str | None = None
    slug: str | None = None


class Snapshot(BaseModel):
    """Observed calendar state at a point in time."""

    model_config = ConfigDict(extra="forbid")

    token: str
    calendar_id: str
    day: date_type
    etags: dict[str, str] = Field(default_factory=dict)
    event_ids: dict[str, str] = Field(
        default_factory=dict,
        description="uid -> provider event id. Keeps uid opaque: never derive "
        "one identifier from the other's string form.",
    )


def make_snapshot(
    calendar_id: str, day: date_type, events: list[CalendarEvent]
) -> Snapshot:
    """Build a snapshot from observed events."""
    etags = {event.event_id: event.etag for event in events}
    event_ids = {event.uid: event.event_id for event in events if event.uid}
    payload = json.dumps(
        {"calendar_id": calendar_id, "day": day.isoformat(), "etags": etags},
        sort_keys=True,
    )
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return Snapshot(
        token=token,
        calendar_id=calendar_id,
        day=day,
        etags=etags,
        event_ids=event_ids,
    )


def drift(snapshot: Snapshot, live: list[CalendarEvent]) -> list[str]:
    """Event ids that changed, appeared, or vanished since the snapshot.

    A single dict comparison covers all three cases: an id present in both
    with a different etag is a concurrent edit; an id present live but
    absent from the snapshot never existed when the snapshot was taken
    (``snapshot.etags.get`` returns ``None``, which never equals a real
    etag); an id present in the snapshot but absent live has vanished.
    """
    live_etags = {event.event_id: event.etag for event in live}
    changed = {
        event_id
        for event_id, etag in live_etags.items()
        if snapshot.etags.get(event_id) != etag
    }
    vanished = set(snapshot.etags) - set(live_etags)
    return sorted(changed | vanished)


class CalendarPort(Protocol):
    """Everything the server needs from a calendar provider."""

    async def list_day(
        self, calendar_id: str, day: date_type
    ) -> list[CalendarEvent]: ...

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent: ...

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent: ...

    async def delete(self, calendar_id: str, event_id: str) -> None: ...


__all__ = ["CalendarEvent", "CalendarPort", "Snapshot", "drift", "make_snapshot"]
