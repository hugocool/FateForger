from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpsertCalendarEvent:
    user_id: str
    calendar_id: str
    event_id: str
    summary: str
    start: str
    end: str
    time_zone: str | None = None
    color_id: str | None = None
    description: str | None = None


__all__ = ["UpsertCalendarEvent"]
