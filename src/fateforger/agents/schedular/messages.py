from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SuggestNextSlot:
    calendar_id: str = "primary"
    duration_min: int = 30
    time_zone: str = "Europe/Amsterdam"
    horizon_days: int = 2
    work_start_hour: int = 9
    work_end_hour: int = 18


@dataclass(frozen=True)
class SuggestedSlot:
    ok: bool
    start_utc: str | None = None
    end_utc: str | None = None
    time_zone: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class UpsertCalendarEvent:
    calendar_id: str
    event_id: str
    summary: str
    description: str | None
    start: str
    end: str
    time_zone: str
    color_id: str | None = None


@dataclass(frozen=True)
class UpsertCalendarEventResult:
    ok: bool
    calendar_id: str | None = None
    event_id: str | None = None
    event_url: str | None = None
    error: str | None = None


__all__ = [
    "SuggestNextSlot",
    "SuggestedSlot",
    "UpsertCalendarEvent",
    "UpsertCalendarEventResult",
]
