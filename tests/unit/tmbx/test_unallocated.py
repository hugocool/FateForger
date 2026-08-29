# tests/unit/tmbx/test_unallocated.py
from __future__ import annotations

from datetime import date, time, timedelta

from tmbx.core.models import (
    ET,
    AfterPrev,
    BeforeNext,
    Block,
    FixedStart,
    FixedWindow,
    Plan,
)
from tmbx.core.unallocated import unallocated


def _p(**kw):
    return Plan(date=date(2026, 8, 17), **kw)


def _rows(plan):
    return [gap.model_dump(mode="json") for gap in unallocated(plan)]


def test_an_interior_gap_is_reported_with_both_neighbours():
    """DW1 ends 11:30, GY1 starts 14:30 — three hours nothing claims.

    The handles on either side are the load-bearing part: a gap an agent can
    anchor to is one it can act on, where a bare duration is only a complaint.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 30), dur=timedelta(hours=2)), anchor_source="user"),
        Block(uid="u2", h="GY1", n="Gym", t=ET.H,
              p=FixedStart(st=time(14, 30), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "11:30:00", "end": "14:30:00", "duration": "PT3H",
         "after": "DW1", "before": "GY1"}
    ]


def test_a_background_window_makes_leading_and_trailing_time_reportable():
    """``BG`` is this codebase's boundary concept: it does not occupy chain
    time and is excluded from the overlap check. Declaring 09:00-17:00
    available is what makes the two hours before DW1 and the four after it
    unallocated rather than merely outside the plan.

    Neither gap has a block on one side, and ``None`` says so rather than
    naming BG1 — a background window is the boundary, not a neighbour.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="BG1", n="Available", t=ET.BG,
              p=FixedWindow(st=time(9, 0), et=time(17, 0)), anchor_source="calendar"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(11, 0), dur=timedelta(hours=2)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "09:00:00", "end": "11:00:00", "duration": "PT2H",
         "after": None, "before": "DW1"},
        {"start": "13:00:00", "end": "17:00:00", "duration": "PT4H",
         "after": "DW1", "before": None},
    ]


def test_time_outside_every_background_window_is_never_leading_or_trailing():
    """The window bounds the claim. DW1 runs 07:00-08:00, before the
    declared 09:00 start, and DW2 runs 17:00-18:00, past the declared end —
    so neither the hour before DW1 nor the hour after DW2 is reportable.
    Only the interior gap, and the part of the window nothing claims,
    survive.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="BG1", n="Available", t=ET.BG,
              p=FixedWindow(st=time(9, 0), et=time(17, 0)), anchor_source="calendar"),
        Block(uid="u2", h="DW1", n="Early", t=ET.DW,
              p=FixedStart(st=time(7, 0), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u3", h="DW2", n="Late", t=ET.DW,
              p=FixedStart(st=time(17, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "08:00:00", "end": "17:00:00", "duration": "PT9H",
         "after": "DW1", "before": "DW2"},
    ]


def test_back_to_back_blocks_report_no_gap():
    """A zero-length gap is not a gap. DW1 ends exactly when DW2 starts, and
    reporting an instantaneous hole between every adjacent pair would bury
    the real gaps under noise."""
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u2", h="DW2", n="More", t=ET.DW, p=AfterPrev(dur=timedelta(hours=1))),
    ])
    assert _rows(plan) == []


def test_without_a_background_window_only_the_interior_is_measured():
    """No ``BG`` block means the day has declared neither a start nor an
    end. The gap between the two blocks is still real; the night before and
    the night after are not tmbx's to claim, and midnight-to-midnight would
    report the user's sleep as unallocated time."""
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u2", h="GY1", n="Gym", t=ET.H,
              p=FixedStart(st=time(14, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "10:00:00", "end": "14:00:00", "duration": "PT4H",
         "after": "DW1", "before": "GY1"},
    ]


def test_overlapping_blocks_never_manufacture_a_gap():
    """A plan with an overlap violation still resolves, so this measurement
    still runs on it. DW1 spans 09:00-12:00 while MTG1 and MTG2 sit inside
    it; walking adjacent pairs would see 10:00-10:30 free between the two
    meetings, but DW1 is sitting in it. Blocks cover their union."""
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=3)), anchor_source="user"),
        Block(uid="u2", h="MTG1", n="Standup", t=ET.M,
              p=FixedStart(st=time(9, 30), dur=timedelta(minutes=30)), anchor_source="calendar"),
        Block(uid="u3", h="MTG2", n="Review", t=ET.M,
              p=FixedStart(st=time(10, 30), dur=timedelta(minutes=30)), anchor_source="calendar"),
    ])
    assert _rows(plan) == []


def test_a_plan_of_only_background_blocks_reports_the_whole_window():
    """Availability declared and nothing placed in it. Both neighbours are
    ``None`` — honestly, because there is no block on either side — and the
    window itself is the answer: the entire day is unallocated."""
    plan = _p(blocks=[
        Block(uid="u1", h="BG1", n="Available", t=ET.BG,
              p=FixedWindow(st=time(9, 0), et=time(17, 0)), anchor_source="calendar"),
    ])
    assert _rows(plan) == [
        {"start": "09:00:00", "end": "17:00:00", "duration": "PT8H",
         "after": None, "before": None},
    ]


def test_an_empty_plan_reports_nothing():
    """No blocks and no declared availability: nothing to measure, and
    nothing to raise on either."""
    assert _rows(_p(blocks=[])) == []


def test_a_plan_that_cannot_resolve_reports_nothing():
    """DW0 (bn) immediately followed by DW1 (ap) is a circular chain —
    ``resolve`` refuses it. There are no times to subtract, and the caller
    already holds a ``Violation`` saying why; inventing gaps from a plan
    that has no resolved times would be worse than saying nothing."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW0", n="Wait", t=ET.DW, p=BeforeNext(dur=timedelta(minutes=15))),
        Block(uid="u3", h="DW1", n="Work", t=ET.DW, p=AfterPrev(dur=timedelta(hours=1))),
    ])
    assert _rows(plan) == []


def test_overlapping_background_windows_are_one_region_not_two():
    """Two availability windows that overlap declare one availability. The
    morning gap must be reported once, not once per window that contains
    it."""
    plan = _p(blocks=[
        Block(uid="u1", h="BG1", n="Morning", t=ET.BG,
              p=FixedWindow(st=time(9, 0), et=time(13, 0)), anchor_source="calendar"),
        Block(uid="u2", h="BG2", n="Afternoon", t=ET.BG,
              p=FixedWindow(st=time(11, 0), et=time(17, 0)), anchor_source="calendar"),
        Block(uid="u3", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(12, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "09:00:00", "end": "12:00:00", "duration": "PT3H",
         "after": None, "before": "DW1"},
        {"start": "13:00:00", "end": "17:00:00", "duration": "PT4H",
         "after": "DW1", "before": None},
    ]


def test_a_background_window_inside_the_chain_adds_nothing():
    """The window declares time available that a block already occupies.
    Availability is a region to measure, not time to report twice."""
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=3)), anchor_source="user"),
        Block(uid="u2", h="BG1", n="Available", t=ET.BG,
              p=FixedWindow(st=time(10, 0), et=time(11, 0)), anchor_source="calendar"),
    ])
    assert _rows(plan) == []


def test_gaps_are_measured_on_datetimes_not_on_wall_clock_times():
    """PR1 is a window that crosses midnight (23:00 on day D to 01:00 on
    D+1). DW1 is fixed at 02:00, which a ``FixedStart`` always interprets
    against the plan's own date — so DW1 runs on day D, twenty hours *before*
    PR1, not one hour after it.

    Subtracting the wall-clock ``end``/``start`` fields would read PR1 as
    ending at 01:00 and DW1 as starting at 02:00 and report a tidy one-hour
    gap. The real free stretch is the twenty hours between DW1's end and
    PR1's start, and the handles come out the other way round. Plan order is
    deliberately the reverse of clock order here, so the chronological sort
    is under test too.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Late shift", t=ET.PR,
              p=FixedWindow(st=time(23, 0), et=time(1, 0)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Early work", t=ET.DW,
              p=FixedStart(st=time(2, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "03:00:00", "end": "23:00:00", "duration": "PT20H",
         "after": "DW1", "before": "PR1"},
    ]


def test_a_gap_crossing_midnight_reports_its_true_duration():
    """The availability window runs 20:00 to 04:00 the next morning. The
    trailing gap's ``end`` reads as earlier than its ``start`` because both
    are times of day — ``duration`` is what carries the truth, and it is
    seven hours, not the negative sixteen a clock subtraction would give."""
    plan = _p(blocks=[
        Block(uid="u1", h="BG1", n="Night shift window", t=ET.BG,
              p=FixedWindow(st=time(20, 0), et=time(4, 0)), anchor_source="calendar"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(20, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "21:00:00", "end": "04:00:00", "duration": "PT7H",
         "after": "DW1", "before": None},
    ]


def test_a_backward_resolved_block_bounds_a_gap_like_any_other():
    """DW0 is ``bn``: it has no times of its own until the backward pass
    pins it to end when DW1 starts. The gap before it must be measured
    against that resolved 13:30, not against the plan's list order."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u2", h="DW0", n="Prep", t=ET.DW, p=BeforeNext(dur=timedelta(minutes=30))),
        Block(uid="u3", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(14, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "10:00:00", "end": "13:30:00", "duration": "PT3H30M",
         "after": "PR1", "before": "DW0"},
    ]


def test_the_neighbour_named_is_deterministic_when_two_blocks_end_together():
    """DW1 (09:00-11:00) and MTG1 (10:00-11:00) both end at 11:00, so either
    could be called the block before the gap. The answer is fixed, not
    incidental: the latest-sorting block ending there — the one a reader
    would call the block just before — under the same ordering ``resolve``
    uses for its own overlap check."""
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=2)), anchor_source="user"),
        Block(uid="u2", h="MTG1", n="Standup", t=ET.M,
              p=FixedStart(st=time(10, 0), dur=timedelta(hours=1)), anchor_source="calendar"),
        Block(uid="u3", h="GY1", n="Gym", t=ET.H,
              p=FixedStart(st=time(13, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "11:00:00", "end": "13:00:00", "duration": "PT2H",
         "after": "MTG1", "before": "GY1"},
    ]


def test_several_interior_gaps_come_back_in_chronological_order():
    """The list is ordered by clock, not by the order blocks appear in the
    plan — patch ops are set-semantic, so plan order carries no chronology."""
    plan = _p(blocks=[
        Block(uid="u3", h="GY1", n="Gym", t=ET.H,
              p=FixedStart(st=time(18, 0), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u2", h="DW2", n="More", t=ET.DW,
              p=FixedStart(st=time(13, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert [(g["start"], g["after"], g["before"]) for g in _rows(plan)] == [
        ("10:00:00", "DW1", "DW2"),
        ("14:00:00", "DW2", "GY1"),
    ]


def test_a_zero_length_block_does_not_split_a_gap_in_two():
    """A ``fw`` window with ``et == st`` is a legal same-day zero-duration
    block — ``_resolve_window`` says so explicitly. It occupies no time, so
    it cannot cover any of the gap it sits in, and reporting 10:00-11:00 and
    11:00-13:00 instead of one 10:00-13:00 stretch would split a real three
    hours into two smaller ones that read as less worth asking about.

    Written after a mutation run: replacing the union over occupied
    intervals with a plain sort left every other test green, and this is the
    case that separates them.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 0), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u2", h="ZRO1", n="Instant", t=ET.M,
              p=FixedWindow(st=time(11, 0), et=time(11, 0)), anchor_source="calendar"),
        Block(uid="u3", h="GY1", n="Gym", t=ET.H,
              p=FixedStart(st=time(13, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert _rows(plan) == [
        {"start": "10:00:00", "end": "13:00:00", "duration": "PT3H",
         "after": "DW1", "before": "GY1"},
    ]


def test_a_zero_length_availability_window_declares_nothing():
    """The mirror of the block case: a ``BG`` window with ``et == st``
    declares an instant, not a stretch, so there is no availability to
    report free — and certainly not a zero-length gap."""
    plan = _p(blocks=[
        Block(uid="u1", h="BG1", n="Available", t=ET.BG,
              p=FixedWindow(st=time(12, 0), et=time(12, 0)), anchor_source="calendar"),
    ])
    assert _rows(plan) == []
