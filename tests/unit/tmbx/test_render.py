from __future__ import annotations

from datetime import date, time, timedelta

from tmbx.core.models import ET, AfterPrev, Block, FixedWindow, Plan
from tmbx.core.render import render_plan


def _plan():
    return Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="DW1", n="Sprint", t=ET.DW,
                  p=FixedWindow(st=time(9, 0), et=time(10, 30)), anchor_source="user"),
            Block(uid="u2", h="LUN1", n="Lunch", t=ET.R,
                  p=AfterPrev(dur=timedelta(minutes=45))),
        ],
    )


def test_handle_is_the_first_column():
    header = render_plan(_plan()).splitlines()[0]
    assert header.split(",")[0].strip().endswith("H")


def test_all_four_modes_are_distinguishable():
    body = render_plan(_plan())
    assert "fw" in body
    assert "ap" in body
    assert "true" not in body and "false" not in body


def test_durations_are_iso_not_seconds():
    body = render_plan(_plan())
    assert "PT45M" in body
    assert "2700" not in body


def test_resolved_times_are_present():
    body = render_plan(_plan())
    assert "09:00" in body and "10:30" in body


def test_uid_is_never_rendered():
    body = render_plan(_plan())
    assert "u1" not in body and "u2" not in body


def test_empty_plan_renders_header_only():
    plan = Plan(date=date(2026, 8, 17), blocks=[])
    assert len(render_plan(plan).strip().splitlines()) == 1


def test_midnight_crossing_end_gets_a_day_offset_marker():
    """A FixedWindow may legitimately end earlier, by the clock, than it
    started (an overnight block). The bare wall-clock time alone (23:00 ->
    01:00) would look like a data error -- a negative span with nothing to
    say otherwise. The render must mark the day change instead of hiding it.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u3", h="OVN1", n="Night shift", t=ET.DW,
                  p=FixedWindow(st=time(23, 0), et=time(1, 0)), anchor_source="user"),
        ],
    )
    body = render_plan(plan)
    assert "23:00" in body
    assert "01:00+1" in body
