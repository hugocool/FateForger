"""In-memory calendar for tests. No network, ever.

Task 14's precondition tests need a calendar that changes *between* a
snapshot and a commit — the exact scenario the legacy engine gets wrong,
and one that cannot be exercised against a real calendar. ``mutate()``
exists purely to simulate a concurrent edit made by someone else, bumping
an event's etag without touching its content.

**A fake that is kinder than the port is worse than no fake.** Everything
handed back here goes through ``_as_fetched``, which drops ``link_url`` the
way a real adapter does: the url is written into the provider's description
and never read back (``calendar.port.CalendarEvent``, ``gcal.py``), so an
event fetched from Google carries ``link_id`` and ``link_url is None``. This
fake used to echo the url straight back, and that one extra field of
generosity hid two Criticals on the same branch — a doubled composed
description, and an ``undo`` that stripped the url off every linked event
for good. Neither could fail here, because here the url was always still
there. Fidelity a test relies on has to be fidelity production has.
"""

from __future__ import annotations

from datetime import date as date_type

from .port import CalendarEvent


def _as_fetched(event: CalendarEvent) -> CalendarEvent:
    """One stored event as a provider would hand it back.

    A deep copy — nothing here shares mutable state with a caller — with
    ``link_url`` dropped. See the module docstring for why that field and
    no other: it is the one half of a link that does not round-trip
    through a real provider, so it is the one field a fake can lie about
    without anybody noticing until production.
    """
    return event.model_copy(deep=True, update={"link_url": None})


class FakeCalendar:
    """Implements ``CalendarPort`` over an in-memory dict."""

    backend = "fake"
    durable = False

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
            _as_fetched(event)
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
        return _as_fetched(stored)

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        items = self._events.setdefault(calendar_id, [])
        for index, existing in enumerate(items):
            if existing.event_id == event.event_id:
                stored = event.model_copy(deep=True)
                self._version += 1
                stored.etag = f"v{self._version}"
                items[index] = stored
                return _as_fetched(stored)
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
