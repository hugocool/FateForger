"""Policy for the planning session that starts itself.

Every value here is a decision Hugo made on 2026-09-04, kept as data so the
harness port (#164) can lift it into a skill or config without re-deciding.
Nothing in this module does I/O and nothing reads what a user wrote.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

#: Reminder kinds the reconciler emits beside its nudges.
SESSION_START_KIND = "session_start"
SESSION_EXPIRE_KIND = "session_expire"

#: A session starting before this hour (in the event's own timezone) plans the
#: event's day; from this hour on it plans the next day. #282 replaces this
#: rule with the day recorded on the event.
DAY_CUTOFF_HOUR = 14

#: Minutes after the open at which the Admonisher speaks, measured from the
#: moment the DM link is posted.
LADDER_OFFSETS: tuple[timedelta, ...] = (
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(minutes=20),
    timedelta(minutes=40),
)

#: How recently a session must have been written to count as one the user is
#: working in, when its revision alone cannot say. A hand-opened session sits
#: at revision 1 until its first turn lands, so expiry reads the clock instead.
LIVE_RECENCY = timedelta(minutes=10)

#: One line per rung, escalating. `{permalink}` is the session thread,
#: `{start}` the event's start as HH:MM.
NUDGE_LINES: tuple[str, ...] = (
    "Your planning session is open — {permalink}",
    "Still waiting — the day isn't planned yet — {permalink}",
    "{start} has passed. Ten minutes in — {permalink}",
    "Twenty minutes. Plan the day or tell me when you will — {permalink}",
    "Last call: the session closes at the end of the hour — {permalink}",
)


def planning_day_for(event_start: datetime) -> date:
    """Which day a session starting at ``event_start`` plans. Host arithmetic."""

    if event_start.tzinfo is None:
        raise ValueError("event_start must carry a timezone; the cutoff is local")
    if event_start.hour < DAY_CUTOFF_HOUR:
        return event_start.date()
    return event_start.date() + timedelta(days=1)


def nudge_line(attempt: int, *, permalink: str, start: str) -> str:
    """The Admonisher's line for rung ``attempt`` (0-based); past the end, the last."""

    index = min(max(attempt, 0), len(NUDGE_LINES) - 1)
    return NUDGE_LINES[index].format(permalink=permalink, start=start)


def dm_open_line(*, day_label: str, permalink: str) -> str:
    return f"Your planning session for {day_label} is open — {permalink}"


def missed_line() -> str:
    return "Missed today's planning session."


__all__ = [
    "DAY_CUTOFF_HOUR",
    "LADDER_OFFSETS",
    "LIVE_RECENCY",
    "NUDGE_LINES",
    "SESSION_EXPIRE_KIND",
    "SESSION_START_KIND",
    "dm_open_line",
    "missed_line",
    "nudge_line",
    "planning_day_for",
]
