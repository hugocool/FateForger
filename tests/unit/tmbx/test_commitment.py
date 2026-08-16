# tests/unit/tmbx/test_commitment.py
from __future__ import annotations

from datetime import date, time, timedelta

from tmbx.core.commitment import overspecified
from tmbx.core.models import ET, AfterPrev, BeforeNext, Block, FixedStart, FixedWindow, Plan


def _p(**kw):
    return Plan(date=date(2026, 8, 17), **kw)


def test_redundant_fixed_start_is_flagged():
    """DW1 at 09:30 is exactly where ap would put it — the pin buys nothing."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 30), dur=timedelta(minutes=90)), anchor_source="user"),
    ])
    assert overspecified(plan) == ["DW1"]


def test_load_bearing_fixed_start_is_not_flagged():
    """DW1 at 11:00 leaves a gap ap cannot reproduce."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(11, 0), dur=timedelta(minutes=90)), anchor_source="user"),
    ])
    assert overspecified(plan) == []


def test_first_anchor_is_never_flagged():
    """Removing the only anchor would leave the chain unanchored."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
    ])
    assert overspecified(plan) == []


def test_already_minimal_plan_is_clean():
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
        Block(uid="u3", h="DW2", n="More", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=45))),
    ])
    assert overspecified(plan) == []


def test_redundant_fixed_window_is_flagged():
    """DW1 as a fixed window (9:30-11:00) lands exactly where ap would place
    it, same duration too — the window pin is redundant, not just fs pins."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedWindow(st=time(9, 30), et=time(11, 0)), anchor_source="user"),
    ])
    assert overspecified(plan) == ["DW1"]


def test_midnight_crossing_anchor_is_not_falsely_flagged():
    """PR1 (22:00, 2h) crosses midnight and ends at 00:00 the *next* day.
    DW1 is fixed at 00:00-01:00 *that same day D* (a FixedStart's ``st`` is
    always interpreted against the plan's own date) — so its wall-clock
    start/end (00:00, 01:00) happen to equal what relaxing it to ``ap``
    would produce, but the ``ap`` placement actually lands a full 24h later
    (right after PR1, on day D+1). Comparing bare ``time`` values (the
    ``Resolved.start``/``end`` fields) loses the day and would wrongly
    treat these as equal; comparing ``start_dt``/``end_dt`` must not.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Late shift", t=ET.PR,
              p=FixedStart(st=time(22, 0), dur=timedelta(hours=2)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Early work", t=ET.DW,
              p=FixedStart(st=time(0, 0), dur=timedelta(hours=1)), anchor_source="user"),
    ])
    assert overspecified(plan) == []


def test_background_block_is_never_flagged_and_does_not_crash():
    """BG blocks sit outside the ap/fs/fw chain entirely — ``Block`` itself
    forbids a BG block from taking ``ap`` timing. ``overspecified`` must
    skip them rather than attempting (and failing) that relaxation."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="BG1", n="Lunch block", t=ET.BG,
              p=FixedStart(st=time(12, 0), dur=timedelta(minutes=60)), anchor_source="calendar"),
        Block(uid="u3", h="DW1", n="Work", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
    ])
    assert overspecified(plan) == []


def test_first_anchor_exemption_ignores_preceding_non_anchor_blocks():
    """A ``bn`` block ahead of the plan's only fs/fw anchor in list order
    must not consume the first-anchor exemption — the exemption belongs to
    the first fs/fw block in the chain, not to whatever sorts first."""
    plan = _p(blocks=[
        Block(uid="u1", h="DW1", n="Prep", t=ET.DW, p=BeforeNext(dur=timedelta(minutes=15))),
        Block(uid="u2", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
    ])
    assert overspecified(plan) == []
