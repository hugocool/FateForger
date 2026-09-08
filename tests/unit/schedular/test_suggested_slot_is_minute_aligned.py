"""A suggested slot carrying seconds poisons every plan built around it.

Measured on 2026-08-31, the first session against the real calendar. The
Admonisher's own "Daily planning session" event was written as

    start 2026-08-31T13:40:18+02:00   end 14:10:18+02:00

because the slot search does `window_start = max(window_start, now + 5min)` and
`now` carries seconds: suggested at 13:35:18, plus five minutes, is 13:40:18.

tmbx then read that event faithfully and the planner anchored a block
`after` it, so the whole downstream chain inherited eighteen seconds until it
met a block anchored to a clean wall-clock time:

    Overlap: SW2 ends 18:00:18 but GY1 starts 18:00:00

`plan_apply` refused, correctly. Four attempts, the retry budget spent, and the
turn died. **No day could be committed at all** while such an event existed.

It hid for months because every prior session ran against an empty in-memory
calendar, where nothing is ever anchored after anything real.

Rounding is *up*, never down: rounding a start down moves it into the busy
interval it was placed after, manufacturing the overlap this exists to prevent.
"""

import datetime as dt

from fateforger.agents.schedular.agent import _ceil_to_minute


def test_seconds_are_rounded_up_to_the_next_minute() -> None:
    got = _ceil_to_minute(dt.datetime(2026, 8, 31, 13, 40, 18))
    assert got == dt.datetime(2026, 8, 31, 13, 41, 0)


def test_an_already_aligned_time_does_not_move() -> None:
    """Rounding up must not push a clean slot a minute later every pass."""

    exact = dt.datetime(2026, 8, 31, 13, 40, 0)
    assert _ceil_to_minute(exact) == exact


def test_microseconds_count_as_a_partial_minute() -> None:
    got = _ceil_to_minute(dt.datetime(2026, 8, 31, 13, 40, 0, 1))
    assert got == dt.datetime(2026, 8, 31, 13, 41, 0)


def test_the_timezone_survives() -> None:
    """The slot is serialised through astimezone, so a dropped tz would move it."""

    tz = dt.timezone(dt.timedelta(hours=2))
    got = _ceil_to_minute(dt.datetime(2026, 8, 31, 13, 40, 18, tzinfo=tz))
    assert got.tzinfo is tz
    assert got.minute == 41 and got.second == 0


def test_an_hour_boundary_carries() -> None:
    got = _ceil_to_minute(dt.datetime(2026, 8, 31, 13, 59, 30))
    assert got == dt.datetime(2026, 8, 31, 14, 0, 0)
