from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import pytest

from fateforger.haunt import session_start
from fateforger.haunt.session_start import (
    LADDER_OFFSETS,
    NUDGE_LINES,
    dm_open_line,
    missed_line,
    nudge_line,
    planning_day_for,
)

AMS = ZoneInfo("Europe/Amsterdam")


def test_a_morning_event_plans_its_own_day() -> None:
    assert planning_day_for(datetime(2026, 9, 4, 9, 0, tzinfo=AMS)) == date(2026, 9, 4)


def test_an_evening_event_plans_the_next_day() -> None:
    assert planning_day_for(datetime(2026, 9, 4, 18, 0, tzinfo=AMS)) == date(2026, 9, 5)


def test_the_cutoff_is_fourteen_in_the_events_own_zone() -> None:
    assert planning_day_for(datetime(2026, 9, 4, 13, 59, tzinfo=AMS)) == date(2026, 9, 4)
    assert planning_day_for(datetime(2026, 9, 4, 14, 0, tzinfo=AMS)) == date(2026, 9, 5)
    # 13:00 UTC is 15:00 Amsterdam: an afternoon session, planning tomorrow.
    utc_afternoon = datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("UTC")).astimezone(AMS)
    assert planning_day_for(utc_afternoon) == date(2026, 9, 5)


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        planning_day_for(datetime(2026, 9, 4, 9, 0))


def test_the_ladder_is_the_agreed_five_offsets() -> None:
    assert LADDER_OFFSETS == tuple(timedelta(minutes=m) for m in (2, 5, 10, 20, 40))
    assert len(NUDGE_LINES) == len(LADDER_OFFSETS)


def test_every_nudge_line_carries_the_permalink() -> None:
    for attempt in range(len(NUDGE_LINES)):
        line = nudge_line(attempt, permalink="https://x/p", start="09:00")
        assert "https://x/p" in line
    assert "09:00" in nudge_line(2, permalink="https://x/p", start="09:00")


def test_an_attempt_past_the_ladder_uses_the_last_line() -> None:
    assert nudge_line(99, permalink="p", start="s") == nudge_line(4, permalink="p", start="s")


def test_open_and_missed_lines() -> None:
    assert "https://x/p" in dm_open_line(day_label="Fri 4 Sep", permalink="https://x/p")
    assert "Fri 4 Sep" in dm_open_line(day_label="Fri 4 Sep", permalink="https://x/p")
    assert missed_line()


def test_the_policy_module_never_reads_user_text() -> None:
    """CLAUDE.md: nothing here may judge what a user wrote."""

    tree = ast.parse(inspect.getsource(session_start))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "re" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"lower", "upper", "startswith", "endswith", "split", "find"}
