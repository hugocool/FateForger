"""In-memory calendar for tests. No network, ever.

Task 14's precondition tests need a calendar that changes *between* a
snapshot and a commit — the exact scenario the legacy engine gets wrong,
and one that cannot be exercised against a real calendar. ``mutate()``
exists purely to simulate a concurrent edit made by someone else, bumping
an event's etag without touching its content.
"""

from __future__ import annotations

from datetime import date as date_type

from .port import CalendarEvent


class FakeCalendar:
    """Implements ``CalendarPort`` over an in-memory dict."""

    def __init__(self, events: dict[str, list[CalendarEvent]] | None = None) -> None:
        self._events: dict[str, list[CalendarEvent]] = {
            key: [event.model_copy(deep=True) for event in value]
            for key, value in (events or {}).items()
        }
        self._version = 1

    async def list_day(
        self, calendar_id: str, day: date_type, tz: str
    ) -> list[CalendarEvent]:
        """``tz`` is accepted for interface parity with a real adapter,
        which needs it to compute the day's UTC boundaries and to convert
        provider-side aware timestamps down to naive wall-clock time. The
        fake already stores naive datetimes that agree with the caller's
        chosen timezone, so no conversion happens here — it is not
        validated against ``tz`` either, since the fake has no aware
        timestamps to check it against."""
        return [
            event.model_copy(deep=True)
            for event in self._events.get(calendar_id, [])
            if event.start.date() == day
        ]

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        items = self._events.setdefault(calendar_id, [])
        if any(existing.event_id == event.event_id for existing in items):
            raise ValueError(
                f"event {event.event_id!r} already exists in calendar "
                f"{calendar_id!r}"
            )
        stored = event.model_copy(deep=True)
        if not stored.etag:
            stored.etag = "v1"
        items.append(stored)
        return stored.model_copy(deep=True)

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        items = self._events.setdefault(calendar_id, [])
        for index, existing in enumerate(items):
            if existing.event_id == event.event_id:
                stored = event.model_copy(deep=True)
                self._version += 1
                stored.etag = f"v{self._version}"
                items[index] = stored
                return stored.model_copy(deep=True)
        raise KeyError(event.event_id)

    async def delete(self, calendar_id: str, event_id: str) -> None:
        items = self._events.setdefault(calendar_id, [])
        self._events[calendar_id] = [e for e in items if e.event_id != event_id]

    def mutate(self, calendar_id: str, event_id: str) -> None:
        """Simulate an edit made elsewhere — bumps the etag only.

        Scoped to ``calendar_id`` like every sibling method. Two different
        calendars can legitimately share an ``event_id`` string, and this
        method exists to give Task 14's precondition tests fidelity — a
        version that searched every calendar and bumped the first match
        would silently mutate the wrong calendar's event the moment that
        collision happens, defeating the tests it exists to support.
        """
        if calendar_id not in self._events:
            raise KeyError(calendar_id)
        for event in self._events[calendar_id]:
            if event.event_id == event_id:
                self._version += 1
                event.etag = f"v{self._version}"
                return
        raise KeyError(event_id)


__all__ = ["FakeCalendar"]
