from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from tmbx.core.models import ET, AfterPrev, BeforeNext, Block, FixedStart, FixedWindow, Plan
from tmbx.core.render import EMPTY_DAY_LINE, render_plan


def _named(name, p, *, h="XX1", t=ET.DW, anchor_source="user"):
    """A single-block plan whose only block has the given name/timing --
    used by the escaping and duration-formatting tests below, where the
    rest of the block is incidental.
    """
    return Plan(
        date=date(2026, 8, 17),
        blocks=[Block(uid="u1", h=h, n=name, t=t, p=p, anchor_source=anchor_source)],
    )


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


def test_empty_plan_says_so_instead_of_trailing_off(  # #253 item 3
):
    """``plan_read`` on 2026-09-02 returned a column spec, a colon, and
    nothing -- indistinguishable from a truncated string to whoever reads
    it next. The header keeps the ``blocks[0]`` count (it is the table's
    contract), and one sentence after it says the day is empty."""
    plan = Plan(date=date(2026, 8, 17), blocks=[])
    header, sentence = render_plan(plan).splitlines()
    assert header.startswith("blocks[0]{")
    assert sentence == EMPTY_DAY_LINE
    assert "empty" in EMPTY_DAY_LINE.lower()


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


def test_negative_day_offset_is_reachable_via_a_backward_resolving_block():
    """bn resolves backwards from the next block's start; anchoring that
    next block near midnight pushes the bn block's own start onto the day
    *before* plan.date -- the negative-offset counterpart to the +1 case
    above, reached without any block itself being fw.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="BN1", n="Wind down", t=ET.R,
                  p=BeforeNext(dur=timedelta(minutes=30))),
            Block(uid="u2", h="EARLY1", n="Early call", t=ET.M,
                  p=FixedWindow(st=time(0, 15), et=time(1, 0)), anchor_source="calendar"),
        ],
    )
    body = render_plan(plan)
    assert "23:45-1" in body
    assert "00:15" in body


def test_every_column_survives_in_the_rendered_row():
    """Pins row *content*, not just the static header -- the header-only
    check in test_handle_is_the_first_column can't catch a column silently
    dropped from (or shifted in) the data rows themselves. Deleting any one
    field from ``_row`` makes one of these two lines fail.
    """
    rows = render_plan(_plan()).splitlines()[1:]
    assert rows[0] == "DW1,tmbx,DW,Sprint,09:00,10:30,fw,PT1H30M,"
    assert rows[1] == "LUN1,tmbx,R,Lunch,10:30,11:15,ap,PT45M,"


def test_own_column_defaults_to_tmbx_when_no_foreign_uids_given():
    rows = render_plan(_plan()).splitlines()[1:]
    assert all(row.split(",")[1] == "tmbx" for row in rows)


def test_foreign_uids_are_marked_foreign_and_others_stay_tmbx():
    """The only signal in a rendered plan that distinguishes an immovable
    block from an editable one -- see the module docstring. A block's
    ``uid`` (never itself rendered) is the join key, not its handle:
    handle shape (e.g. an ``EVT``-prefixed one) is only a coincidental,
    unreliable partial signal, per PlanService._plan_from_calendar.
    """
    rows = render_plan(_plan(), foreign_uids={"u2"}).splitlines()[1:]
    by_handle = {row.split(",")[0]: row.split(",")[1] for row in rows}
    assert by_handle["DW1"] == "tmbx"  # uid u1, not in foreign_uids
    assert by_handle["LUN1"] == "foreign"  # uid u2, in foreign_uids


def test_render_survives_a_plan_that_would_fail_overlap_validation():
    """Rendering is a read, not a validator -- confirms plan.resolve's own
    overlap check really would reject this plan, then confirms render_plan
    (via check_overlap=False) does not.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="FST1", n="First", t=ET.DW,
                  p=FixedWindow(st=time(9, 0), et=time(11, 0)), anchor_source="user"),
            Block(uid="u2", h="SND1", n="Second", t=ET.DW,
                  p=FixedWindow(st=time(10, 0), et=time(12, 0)), anchor_source="user"),
        ],
    )
    with pytest.raises(ValueError):
        plan.resolve(check_overlap=True)

    body = render_plan(plan)
    assert "FST1" in body and "SND1" in body


# --- Escaping: free text must not be mistaken for column structure -------


def test_name_containing_the_delimiter_is_quoted():
    body = render_plan(_named("Sprint, planning", FixedWindow(st=time(9, 0), et=time(10, 0))))
    assert body == (
        "blocks[1]{H,own,type,summary,ST,ET,mode,dur,slug}:\n"
        'XX1,tmbx,DW,"Sprint, planning",09:00,10:00,fw,PT1H,'
    )


def test_name_containing_a_newline_is_quoted():
    body = render_plan(_named("Lunch\nBreak", FixedWindow(st=time(9, 0), et=time(10, 0))))
    assert body == (
        "blocks[1]{H,own,type,summary,ST,ET,mode,dur,slug}:\n"
        'XX1,tmbx,DW,"Lunch\nBreak",09:00,10:00,fw,PT1H,'
    )


def test_name_containing_a_quote_is_escaped_by_doubling():
    body = render_plan(_named('Say "hi"', FixedWindow(st=time(9, 0), et=time(10, 0))))
    assert body == (
        "blocks[1]{H,own,type,summary,ST,ET,mode,dur,slug}:\n"
        'XX1,tmbx,DW,"Say ""hi""",09:00,10:00,fw,PT1H,'
    )


def test_empty_name_renders_as_an_empty_field_not_two_quotes():
    body = render_plan(_named("", FixedWindow(st=time(9, 0), et=time(10, 0))))
    assert body == (
        "blocks[1]{H,own,type,summary,ST,ET,mode,dur,slug}:\n"
        "XX1,tmbx,DW,,09:00,10:00,fw,PT1H,"
    )


# --- ISO duration edge cases ----------------------------------------------


def test_duration_sub_minute_renders_in_seconds():
    body = render_plan(_named("Quick", FixedStart(st=time(9, 0), dur=timedelta(seconds=30))))
    assert "PT30S" in body


def test_duration_exactly_one_hour_has_no_minutes_component():
    body = render_plan(_named("Focus", FixedStart(st=time(9, 0), dur=timedelta(hours=1))))
    assert "PT1H" in body


def test_duration_hours_plus_minutes():
    body = render_plan(
        _named("Focus", FixedStart(st=time(9, 0), dur=timedelta(hours=1, minutes=30)))
    )
    assert "PT1H30M" in body


def test_duration_zero_length_window_renders_as_pt0s_not_p0d():
    """FixedWindow permits et == st (models.py: "a same-day, zero-duration
    window"). isodate's own duration_isoformat renders a zero timedelta as
    P0D ("zero days"), which is easy to misread as a whole-day block at a
    glance; PT0S ("a time span of zero") is unambiguous.
    """
    body = render_plan(_named("Placeholder", FixedWindow(st=time(9, 0), et=time(9, 0))))
    assert "PT0S" in body
    assert "P0D" not in body
