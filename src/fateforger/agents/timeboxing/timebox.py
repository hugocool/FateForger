"""Timebox schema and validation for patching workflows.

Also provides conversion functions between the heavy ``Timebox``
(``CalendarEvent``-based, for DB persistence / Slack display) and the
lightweight ``TBPlan`` (for LLM generation / sync engine).
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta
import logging
from typing import List, Optional

from isodate import parse_duration
from pydantic import BaseModel, Field, model_validator

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType

from .tb_models import (
    ET,
    ET_COLOR_MAP,
    AfterPrev,
    BeforeNext,
    FixedStart,
    FixedWindow,
    TBEvent,
    TBPlan,
    gcal_color_to_et,
)

logger = logging.getLogger(__name__)


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

        def _event_label(event: CalendarEvent) -> str:
            """Return a stable human-readable event identifier for errors."""
            uid = getattr(event, "uid", None)
            if isinstance(uid, str) and uid.strip():
                return uid.strip()
            event_id = getattr(event, "eventId", None)
            if isinstance(event_id, str) and event_id.strip():
                return event_id.strip()
            summary = getattr(event, "summary", None)
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
            return "event"

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

        def _time_from_datetime_like(value: object | None) -> time | None:
            """Extract time-of-day from datetime/date/string anchors."""
            if value is None:
                return None
            if isinstance(value, datetime):
                return value.time()
            if isinstance(value, date_type):
                return time.min
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return None
                try:
                    if "T" in text:
                        return datetime.fromisoformat(text).time()
                    date_type.fromisoformat(text)
                    return time.min
                except Exception:
                    return None
            return None

        last_dt: datetime | None = None
        for ev in events:
            # Accept scheduler-style datetime anchors when time-only fields are absent.
            if ev.start_time is None and ev.start is not None:
                ev.start_time = _time_from_datetime_like(ev.start)
            if ev.end_time is None and ev.end is not None:
                ev.end_time = _time_from_datetime_like(ev.end)
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
                    raise ValueError(f"{_event_label(ev)}: needs start or duration")
                if ev.duration is None:
                    raise ValueError(f"{_event_label(ev)}: needs duration")
                ev.start_time = last_dt.time()
                ev.end_time = (last_dt + ev.duration).time()

            if ev.end_time:
                last_dt = datetime.combine(planning_date, ev.end_time)

        next_dt: datetime | None = None
        for ev in reversed(events):
            if (not ev.anchor_prev) and ev.start_time is None and ev.end_time is None:
                if next_dt is None:
                    raise ValueError(f"{_event_label(ev)}: needs end or duration")
                if ev.duration is None:
                    raise ValueError(f"{_event_label(ev)}: needs duration")
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
                        f"{_event_label(last_event)}: start {dt_last_start} is not on {planning_date}"
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
                timeZone=plan.tz,
            )
        )

    return Timebox(events=events, date=plan.date, timezone=plan.tz)


def timebox_to_tb_plan(timebox: Timebox, *, validate: bool = True) -> TBPlan:
    """Convert a heavy ``Timebox`` to a lightweight ``TBPlan``.

    Each ``CalendarEvent`` becomes a ``TBEvent`` with ``FixedWindow``
    timing (since concrete times are already resolved).

    Args:
        timebox: The heavy timebox.
        validate: When ``True`` (default), enforce full ``TBPlan`` validation.
            When ``False``, return a model-constructed plan that may still need
            repair by the Stage 4 patch loop.

    Returns:
        A ``TBPlan`` with ``FixedWindow`` events.
    """
    tb_events: list[TBEvent] = []

    def _time_from_datetime_like(value: object | None) -> time | None:
        """Extract time-of-day from datetime/date/string anchors."""
        if value is None:
            return None
        if isinstance(value, time):
            return value
        if isinstance(value, datetime):
            return value.time().replace(tzinfo=None)
        if isinstance(value, date_type):
            return time.min
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                if "T" in text:
                    return datetime.fromisoformat(text).time().replace(tzinfo=None)
                date_type.fromisoformat(text)
                return time.min
            except Exception:
                return None
        return None

    for ev in timebox.events:
        summary_value = getattr(ev, "summary", None)
        if isinstance(summary_value, str):
            name = summary_value.strip()
        else:
            name = ""
        if not name:
            event_id = getattr(ev, "eventId", None)
            fallback = (
                event_id.strip()
                if isinstance(event_id, str) and event_id.strip()
                else "Busy"
            )
            logger.warning(
                "timebox_to_tb_plan missing summary; using fallback name='%s' event_id='%s'",
                fallback,
                event_id,
            )
            name = fallback

        start_time = ev.start_time or _time_from_datetime_like(getattr(ev, "start", None))
        end_time = ev.end_time or _time_from_datetime_like(getattr(ev, "end", None))
        duration = ev.duration
        start_dt = getattr(ev, "start", None)
        end_dt = getattr(ev, "end", None)
        if (
            duration is None
            and isinstance(start_dt, datetime)
            and isinstance(end_dt, datetime)
            and end_dt > start_dt
        ):
            duration = end_dt - start_dt

        # Map EventType → ET code
        event_type = ev.event_type
        if not isinstance(event_type, EventType):
            try:
                event_type = EventType(event_type)
            except Exception:
                event_type = EventType.MEETING
        et_code_str = _EVENT_TYPE_TO_ET.get(event_type.value, "M")
        et = ET(et_code_str)

        # Use concrete times if available
        if start_time and end_time:
            if duration and end_time <= start_time:
                # Cross-midnight windows cannot be represented as same-day FW;
                # represent as fixed-start + duration.
                timing = FixedStart(st=start_time, dur=duration)
            else:
                timing = FixedWindow(st=start_time, et=end_time)
        elif start_time and duration:
            timing = FixedStart(st=start_time, dur=duration)
        elif end_time and duration and getattr(ev, "anchor_prev", True) is False:
            timing = BeforeNext(dur=duration)
        elif duration:
            timing = AfterPrev(dur=duration)
        else:
            raise ValueError(
                "timebox_to_tb_plan: event cannot be mapped to TB timing "
                f"(summary={name!r})"
            )

        tb_events.append(
            TBEvent(
                n=name,
                d=ev.description or "",
                t=et,
                p=timing,
            )
        )

    payload = {
        "events": tb_events,
        "date": timebox.date,
        "tz": timebox.timezone,
    }
    if validate:
        return TBPlan(**payload)
    return TBPlan.model_construct(**payload)


__all__ = ["CalendarEvent", "Timebox", "tb_plan_to_timebox", "timebox_to_tb_plan"]
