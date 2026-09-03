"""Five attempts is not enough for a day that already has events in it.

Measured on 2026-08-31, the first session to reach a real calendar. The planner
spent its budget and reported, accurately:

    "The patch-retry budget (5 plan_apply attempts) was exhausted mid-fix. The
     candidate is one 5-minute lunch duration change away from resolving: lunch
     currently runs 13:15-13:45 and collides with the foreign Daily planning
     session (13:40-14:10)."

It was one change from converging. Against the empty in-memory calendar every
prior session used, one or two attempts sufficed, because nothing had to be
fitted around anything.

`FF_TIMEBOX_PATCH_MAX_ATTEMPTS` existed to tune this and could not: the
resolver wrapped it in `min(5, ...)`, so raising it did nothing and said
nothing. The knob looked live and was inert -- the same shape as
PLANNING_TIMEZONE, which was read in four places and defined in none.

The budget itself stays: it exists so a model cannot thrash forever, and that
is still worth having.
"""

import pytest

from fateforger.slack_bot.dsh_timebox_attempt_guard_hook import (
    _MAX_ATTEMPTS_ENV,
    _max_attempts,
)


def test_the_environment_can_lower_the_budget_but_not_raise_it(monkeypatch) -> None:
    """It is a safety ceiling, and stays one -- attempts cost ~150s each.

    The first version of this fix removed the ceiling entirely. An existing test
    caught it, and was right to: an unbounded budget only trades a failed turn
    for one nobody will wait out. The number moved on evidence; the shape did
    not.
    """

    monkeypatch.setenv(_MAX_ATTEMPTS_ENV, "3")
    assert _max_attempts() == 3
    monkeypatch.setenv(_MAX_ATTEMPTS_ENV, "99")
    assert _max_attempts() == 8


def test_the_default_fits_a_day_that_has_events_in_it(monkeypatch) -> None:
    """Five was tuned against an empty calendar and measured short on a real one."""

    monkeypatch.delenv(_MAX_ATTEMPTS_ENV, raising=False)
    assert _max_attempts() >= 8


def test_a_budget_below_one_is_still_refused(monkeypatch) -> None:
    """Zero attempts is not a budget, it is a broken planner."""

    monkeypatch.setenv(_MAX_ATTEMPTS_ENV, "0")
    assert _max_attempts() == 1


def test_nonsense_falls_back_rather_than_raising(monkeypatch) -> None:
    """This runs inside a PreToolUse hook; an exception there kills the turn."""

    monkeypatch.setenv(_MAX_ATTEMPTS_ENV, "not a number")
    assert _max_attempts() >= 8
