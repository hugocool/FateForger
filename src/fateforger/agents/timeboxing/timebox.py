"""Timebox schema and validation for patching workflows."""

from __future__ import annotations

from datetime import date as date_type, datetime, time, timedelta
from typing import List, Optional

from isodate import parse_duration
from pydantic import BaseModel, Field, model_validator

from fateforger.agents.schedular.models import CalendarEvent


class Timebox(BaseModel):
    events: List[CalendarEvent] = Field(default_factory=list)
    date: date_type = Field(
        default_factory=date_type.today,
        description="Date the timebox applies to (local date)",
    )
    timezone: str = Field(default="UTC")

    @model_validator(mode="after")
    def schedule_and_validate(self) -> "Timebox":
        """Fill missing start/end/duration fields and reject overlaps."""
        planning_date = self.date or date_type.today()
        events = list(self.events or [])

        def _ensure_time(value: time | str | None) -> time | None:
            """Coerce HH:MM(/:SS) strings into `datetime.time`."""
            if value is None:
                return None
            if isinstance(value, time):
                return value
            return time.fromisoformat(value)

        def _ensure_duration(value: timedelta | str | None) -> timedelta | None:
            """Coerce ISO8601 duration strings into `datetime.timedelta`."""
            if value is None:
                return None
            if isinstance(value, timedelta):
                return value
            return parse_duration(value)

        last_dt: datetime | None = None
        for ev in events:
            ev.start_time = _ensure_time(ev.start_time)
            ev.end_time = _ensure_time(ev.end_time)
            ev.duration = _ensure_duration(ev.duration)
            if ev.start_time and ev.duration and ev.end_time is None:
                ev.end_time = (
                    datetime.combine(planning_date, ev.start_time) + ev.duration
                ).time()
            elif ev.end_time and ev.duration and ev.start_time is None:
                ev.start_time = (
                    datetime.combine(planning_date, ev.end_time) - ev.duration
                ).time()
            elif ev.start_time and ev.end_time and ev.duration is None:
                ev.duration = datetime.combine(
                    planning_date, ev.end_time
                ) - datetime.combine(planning_date, ev.start_time)

            if ev.start_time is None and ev.end_time is None and ev.anchor_prev:
                if last_dt is None:
                    raise ValueError(f"{ev.uid or ev.summary}: needs start or duration")
                if ev.duration is None:
                    raise ValueError(f"{ev.uid or ev.summary}: needs duration")
                ev.start_time = last_dt.time()
                ev.end_time = (last_dt + ev.duration).time()

            if ev.end_time:
                last_dt = datetime.combine(planning_date, ev.end_time)

        next_dt: datetime | None = None
        for ev in reversed(events):
            if (not ev.anchor_prev) and ev.start_time is None and ev.end_time is None:
                if next_dt is None:
                    raise ValueError(f"{ev.uid or ev.summary}: needs end or duration")
                if ev.duration is None:
                    raise ValueError(f"{ev.uid or ev.summary}: needs duration")
                ev.end_time = next_dt.time()
                ev.start_time = (next_dt - ev.duration).time()
            if ev.start_time:
                next_dt = datetime.combine(planning_date, ev.start_time)

        for a, b in zip(events, events[1:]):
            if not a.end_time or not b.start_time:
                raise ValueError("Events must have start/end after scheduling")
            dt_a_end = datetime.combine(planning_date, a.end_time)
            dt_b_start = datetime.combine(planning_date, b.start_time)
            if dt_a_end > dt_b_start:
                a_label = getattr(a, "summary", "event")
                b_label = getattr(b, "summary", "event")
                raise ValueError(
                    f"Overlap: {a_label} → {b_label}"
                )

        if events:
            last_event = events[-1]
            if last_event.start_time:
                dt_last_start = datetime.combine(planning_date, last_event.start_time)
                if dt_last_start.date() != planning_date:
                    raise ValueError(
                        f"{last_event.uid or last_event.summary}: start {dt_last_start} is not on {planning_date}"
                    )

        self.events = events
        return self


__all__ = ["Timebox", "CalendarEvent"]
