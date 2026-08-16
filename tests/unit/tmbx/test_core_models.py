# tests/unit/tmbx/test_core_models.py
from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from pydantic import ValidationError

from tmbx.core.models import ET, AfterPrev, Block, FixedStart, FixedWindow, Plan


def _block(h, t=ET.DW, p=None, uid=None, n="Work"):
    return Block(uid=uid or f"u-{h}", h=h, n=n, t=t, p=p or AfterPrev(dur=timedelta(minutes=60)))


def test_handle_format_accepted():
    assert _block("DW1").h == "DW1"
    assert _block("GYM12").h == "GYM12"


@pytest.mark.parametrize("bad", ["dw1", "D1", "TOOLONG1", "DW", "DW123", "DW-1"])
def test_handle_format_rejected(bad):
    with pytest.raises(ValidationError):
        _block(bad)


def test_resolve_chains_after_prev_from_a_fixed_anchor():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("PR1", t=ET.PR, p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
            _block("DW1", p=AfterPrev(dur=timedelta(minutes=90))),
        ],
    )
    resolved = plan.resolve()
    assert (resolved[0].start, resolved[0].end) == (time(9, 0), time(9, 30))
    assert (resolved[1].start, resolved[1].end) == (time(9, 30), time(11, 0))


def test_fixed_window_infers_duration():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[_block("MT1", t=ET.M, p=FixedWindow(st=time(11, 0), et=time(11, 45)))],
    )
    assert plan.resolve()[0].dur == timedelta(minutes=45)


def test_chain_requires_an_anchor():
    with pytest.raises(ValidationError):
        Plan(
            date=date(2026, 8, 17),
            blocks=[_block("DW1"), _block("DW2")],
        )


def test_handles_must_be_unique_within_a_plan():
    with pytest.raises(ValidationError):
        Plan(
            date=date(2026, 8, 17),
            blocks=[
                _block("DW1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
                _block("DW1"),
            ],
        )


def test_overlap_is_rejected():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("DW1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=120))),
            _block("DW2", p=FixedStart(st=time(10, 0), dur=timedelta(minutes=60))),
        ],
    )
    with pytest.raises(ValueError, match="Overlap"):
        plan.resolve()


def test_background_events_are_exempt_from_overlap():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("DW1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=120))),
            _block("BG1", t=ET.BG, p=FixedWindow(st=time(9, 30), et=time(10, 0))),
        ],
    )
    assert len(plan.resolve()) == 2


def test_background_must_use_fixed_timing():
    with pytest.raises(ValidationError):
        _block("BG1", t=ET.BG, p=AfterPrev(dur=timedelta(minutes=30)))
