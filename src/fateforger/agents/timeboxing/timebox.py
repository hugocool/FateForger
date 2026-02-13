"""Timebox schema and validation for patching workflows.

Also provides conversion functions between the heavy ``Timebox``
(``CalendarEvent``-based, for DB persistence / Slack display) and the
lightweight ``TBPlan`` (for LLM generation / sync engine).
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta
from typing import List, Optional

from isodate import parse_duration
from pydantic import BaseModel, Field, model_validator

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType

from .tb_models import (
    ET,
    ET_COLOR_MAP,
    AfterPrev,
    FixedStart,
    FixedWindow,
    TBEvent,
    TBPlan,
    gcal_color_to_et,
)


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
                raise ValueError(f"Overlap: {a_label} → {b_label}")

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


# ── Conversion: TBPlan ↔ Timebox ─────────────────────────────────────────

# Map ET compact codes to EventType enum members.
_ET_TO_EVENT_TYPE: dict[str, EventType] = {
    "M": EventType.MEETING,
    "C": EventType.COMMUTE,
    "DW": EventType.DEEP_WORK,
    "SW": EventType.SHALLOW_WORK,
    "PR": EventType.PLAN_REVIEW,
    "H": EventType.HABIT,
    "R": EventType.REGENERATION,
    "BU": EventType.BUFFER,
    "BG": EventType.BACKGROUND,
}

_EVENT_TYPE_TO_ET: dict[str, str] = {v.value: k for k, v in _ET_TO_EVENT_TYPE.items()}


def tb_plan_to_timebox(plan: TBPlan) -> Timebox:
    """Convert a lightweight ``TBPlan`` to a heavy ``Timebox``.

    Resolves concrete times and creates ``CalendarEvent`` instances
    for persistence and Slack display.

    Args:
        plan: The lightweight plan.

    Returns:
        A ``Timebox`` with fully resolved ``CalendarEvent`` list.
    """
    resolved = plan.resolve_times()
    events: list[CalendarEvent] = []

    for r in resolved:
        et_code = r["t"]
        event_type = _ET_TO_EVENT_TYPE.get(et_code, EventType.MEETING)

        events.append(
            CalendarEvent(
                summary=r["n"],
                description=r.get("d", ""),
                event_type=event_type,
                start_time=r["start_time"],
                end_time=r["end_time"],
                duration=r.get("duration"),
                timeZone=plan.tz,
            )
        )

    return Timebox(events=events, date=plan.date, timezone=plan.tz)


def timebox_to_tb_plan(timebox: Timebox) -> TBPlan:
    """Convert a heavy ``Timebox`` to a lightweight ``TBPlan``.

    Each ``CalendarEvent`` becomes a ``TBEvent`` with ``FixedWindow``
    timing (since concrete times are already resolved).

    Args:
        timebox: The heavy timebox.

    Returns:
        A ``TBPlan`` with ``FixedWindow`` events.
    """
    tb_events: list[TBEvent] = []

    for ev in timebox.events:
        # Map EventType → ET code
        et_code_str = _EVENT_TYPE_TO_ET.get(ev.event_type.value, "M")
        et = ET(et_code_str)

        # Use concrete times if available
        if ev.start_time and ev.end_time:
            timing = FixedWindow(st=ev.start_time, et=ev.end_time)
        elif ev.start_time and ev.duration:
            timing = FixedStart(st=ev.start_time, dur=ev.duration)
        elif ev.duration:
            timing = AfterPrev(dur=ev.duration)
        else:
            # Fallback: 1-hour after_prev
            timing = AfterPrev(dur=timedelta(hours=1))

        tb_events.append(
            TBEvent(
                n=ev.summary,
                d=ev.description or "",
                t=et,
                p=timing,
            )
        )

    return TBPlan(
        events=tb_events,
        date=timebox.date,
        tz=timebox.timezone,
    )


__all__ = ["CalendarEvent", "Timebox", "tb_plan_to_timebox", "timebox_to_tb_plan"]
