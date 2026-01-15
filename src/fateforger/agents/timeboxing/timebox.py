"""Timebox schema and validation for patching workflows."""

from __future__ import annotations

from datetime import date as date_type, datetime, time, timedelta
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from fateforger.agents.schedular.models import CalendarEvent


class Timebox(BaseModel):
    events: List[CalendarEvent] = Field(default_factory=list)
    date: date_type = Field(
        default_factory=date_type.today,
        description="Date the timebox applies to (local date)",
    )
    timezone: str = Field(default="UTC")

    @model_validator(mode="before")
    def schedule_and_validate(cls, values):  # type: ignore[override]
        planning_date = values.get("date") or date_type.today()
        events = values.get("events") or []

        last_dt: datetime | None = None
        for ev in events:
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

            if ev.start_time is None and ev.end_time is None and not ev.anchor_prev:
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
            if ev.anchor_prev and ev.start_time is None and ev.end_time is None:
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
                raise ValueError(
                    f"Overlap: {a.uid or a.summary} → {b.uid or b.summary}"
                )

        if events:
            last_event = events[-1]
            if last_event.start_time:
                dt_last_start = datetime.combine(planning_date, last_event.start_time)
                if dt_last_start.date() != planning_date:
                    raise ValueError(
                        f"{last_event.uid or last_event.summary}: start {dt_last_start} is not on {planning_date}"
                    )

        values["events"] = events
        return values


__all__ = ["Timebox", "CalendarEvent"]
