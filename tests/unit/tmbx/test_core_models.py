# tests/unit/tmbx/test_core_models.py
from __future__ import annotations

import json
from datetime import date, time, timedelta

import pytest
from pydantic import ValidationError

from tmbx.core.models import (
    ET,
    AfterPrev,
    BeforeNext,
    Block,
    FixedStart,
    FixedWindow,
    Plan,
    PlanViolation,
    Violation,
    ViolationKind,
)


def _block(h, t=ET.DW, p=None, uid=None, n="Work", anchor_source=None):
    p = p or AfterPrev(dur=timedelta(minutes=60))
    if anchor_source is None and p.a in ("fs", "fw"):
        anchor_source = "user"
    return Block(uid=uid or f"u-{h}", h=h, n=n, t=t, p=p, anchor_source=anchor_source)


def test_handle_format_accepted():
    assert _block("DW1").h == "DW1"
    assert _block("GYM12").h == "GYM12"
    assert _block("ABCDE1").h == "ABCDE1"  # 5 letters — max-letters boundary


@pytest.mark.parametrize(
    "bad", ["dw1", "D1", "TOOLONG1", "DW", "DW123", "DW-1", "ABCDEF1"]
)
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


# --- anchor_source enforcement --------------------------------------------


def test_anchor_source_required_for_fixed_start():
    with pytest.raises(ValidationError):
        Block(
            uid="u1",
            h="AA1",
            n="Work",
            t=ET.DW,
            p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
        )


def test_anchor_source_required_for_fixed_window():
    with pytest.raises(ValidationError):
        Block(
            uid="u1",
            h="AA1",
            n="Work",
            t=ET.DW,
            p=FixedWindow(st=time(9, 0), et=time(9, 30)),
        )


def test_anchor_source_not_required_for_after_prev():
    block = Block(
        uid="u1",
        h="AA1",
        n="Work",
        t=ET.DW,
        p=AfterPrev(dur=timedelta(minutes=30)),
    )
    assert block.anchor_source is None


# --- bn: full coverage of the fourth timing mode ---------------------------


def test_bn_resolves_against_a_fixed_successor():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("AA1", p=BeforeNext(dur=timedelta(minutes=20))),
            _block("BB1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
        ],
    )
    aa1 = plan.resolve()[0]
    assert (aa1.start, aa1.end) == (time(8, 40), time(9, 0))


def test_bn_skips_a_bg_block_between_it_and_its_successor():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("AA1", p=BeforeNext(dur=timedelta(minutes=15))),
            _block("BG1", t=ET.BG, p=FixedWindow(st=time(9, 0), et=time(9, 10))),
            _block("CC1", p=FixedStart(st=time(9, 30), dur=timedelta(minutes=30))),
        ],
    )
    resolved = {r.h: r for r in plan.resolve()}
    # Must anchor to CC1 (09:30), not to the BG block (09:00).
    assert (resolved["AA1"].start, resolved["AA1"].end) == (time(9, 15), time(9, 30))


def test_consecutive_bn_blocks_chain_backwards():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("AA1", p=BeforeNext(dur=timedelta(minutes=15))),
            _block("BB1", p=BeforeNext(dur=timedelta(minutes=30))),
            _block("CC1", p=FixedStart(st=time(10, 0), dur=timedelta(minutes=30))),
        ],
    )
    resolved = {r.h: r for r in plan.resolve()}
    assert (resolved["BB1"].start, resolved["BB1"].end) == (time(9, 30), time(10, 0))
    assert (resolved["AA1"].start, resolved["AA1"].end) == (time(9, 15), time(9, 30))


def test_bn_last_with_no_successor_raises():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("AA1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
            _block("BB1", p=BeforeNext(dur=timedelta(minutes=15))),
        ],
    )
    with pytest.raises(ValueError, match="before_next has no following block"):
        plan.resolve()


def test_ap_after_bn_is_circular():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("XA1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
            _block("YB1", p=BeforeNext(dur=timedelta(minutes=15))),
            _block("ZC1", p=AfterPrev(dur=timedelta(minutes=60))),
        ],
    )
    with pytest.raises(ValueError, match="circular"):
        plan.resolve()


# --- FixedWindow crossing midnight -----------------------------------------


def test_fixed_window_crossing_midnight_is_supported():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[_block("NS1", p=FixedWindow(st=time(23, 0), et=time(1, 0)))],
    )
    resolved = plan.resolve()[0]
    assert (resolved.start, resolved.end) == (time(23, 0), time(1, 0))
    assert resolved.dur == timedelta(hours=2)


def test_fixed_window_zero_length_is_same_day_not_midnight_crossing():
    """et == st must stay a same-day zero-duration window, not become 24h.

    Regression: the midnight-crossing rule must be strict (et < st), not
    et <= st — this was correct before that rule existed.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[_block("ZZ1", p=FixedWindow(st=time(9, 0), et=time(9, 0)))],
    )
    resolved = plan.resolve()[0]
    assert (resolved.start, resolved.end) == (time(9, 0), time(9, 0))
    assert resolved.dur == timedelta(0)


def test_cross_midnight_chain_blocks_that_genuinely_overlap_are_rejected():
    """Regression: the overlap check must compare real datetimes, not
    recombine `time`-only start/end with the plan's single date — that
    truncation made a genuine 30-minute overlap across midnight invisible.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("AA1", p=FixedWindow(st=time(22, 0), et=time(0, 0))),
            _block("BB1", p=FixedWindow(st=time(23, 30), et=time(0, 30))),
        ],
    )
    with pytest.raises(ValueError, match="Overlap"):
        plan.resolve()


def test_non_overlapping_fixed_anchors_do_not_overlap_only_because_plan_order_differs():
    """Regression from the Issue #40 live Slack replay.

    Set-semantics additions sharing one insertion anchor are ordered by handle,
    so Dinner can precede Deep Work in the plan list even though their fixed
    clock times are hours apart. Overlap is a time relation, not list order.
    """
    plan = Plan(
        date=date(2026, 8, 29),
        blocks=[
            _block(
                "DIN1",
                n="Dinner",
                p=FixedStart(st=time(19, 0), dur=timedelta(hours=1)),
            ),
            _block(
                "DW1",
                n="Deep engineering 1",
                p=FixedStart(st=time(9, 0), dur=timedelta(minutes=90)),
            ),
        ],
    )

    resolved = plan.resolve()

    assert [row.h for row in resolved] == ["DIN1", "DW1"]


# --- negative-duration invariant --------------------------------------------


def test_negative_duration_is_rejected():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[_block("AA1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=-30)))],
    )
    with pytest.raises(ValueError, match="negative"):
        plan.resolve()


def test_negative_duration_guard_catches_a_nonadjacent_overlap():
    """Regression: a malformed middle block must not let an adjacent-pairs
    overlap check miss a real overlap between the first and third blocks.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("AA1", p=FixedStart(st=time(10, 0), dur=timedelta(minutes=180))),
            _block("BB1", p=FixedStart(st=time(20, 0), dur=timedelta(hours=-10))),
            _block("CC1", p=FixedStart(st=time(10, 30), dur=timedelta(minutes=30))),
        ],
    )
    with pytest.raises(ValueError, match="negative"):
        plan.resolve()


# --- structured violations ---------------------------------------------------
#
# Every failure resolve() can report is a decision point for a user, not just
# an error string: the plan they asked for does not fit and someone has to
# choose what gives way. These assert the *data* a card is built from —
# which blocks, by how much — not the sentence.


def test_overlap_raises_a_typed_violation_naming_both_blocks_and_the_amount():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("DW1", n="Deep Work", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=120))),
            _block("DW2", n="Admin", p=FixedStart(st=time(10, 0), dur=timedelta(minutes=60))),
        ],
    )
    with pytest.raises(PlanViolation) as excinfo:
        plan.resolve()

    violation = excinfo.value.violation
    assert violation.kind is ViolationKind.OVERLAP
    assert [b.h for b in violation.blocks] == ["DW1", "DW2"]
    assert [b.n for b in violation.blocks] == ["Deep Work", "Admin"]
    assert (violation.blocks[0].start, violation.blocks[0].end) == (time(9, 0), time(11, 0))
    assert (violation.blocks[1].start, violation.blocks[1].end) == (time(10, 0), time(11, 0))
    assert violation.magnitude == timedelta(hours=1)


def test_a_typed_violation_is_still_a_value_error():
    """Every existing caller catches ValueError — overspecified(), apply(),
    the server's invalid_patch branch. Narrowing that would silently change
    four call sites' behaviour."""
    assert issubclass(PlanViolation, ValueError)


def _plan(*blocks):
    return Plan(date=date(2026, 8, 17), blocks=list(blocks))


@pytest.mark.parametrize(
    "plan, kind, handles, has_magnitude",
    [
        (
            _plan(
                _block("AA1", p=AfterPrev(dur=timedelta(minutes=30))),
                _block("BB1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
            ),
            "unanchored_after_prev",
            ["AA1"],
            False,
        ),
        (
            _plan(
                _block("AA1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
                _block("BB1", p=BeforeNext(dur=timedelta(minutes=15))),
            ),
            "unanchored_before_next",
            ["BB1"],
            False,
        ),
        (
            _plan(
                _block("XA1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
                _block("YB1", p=BeforeNext(dur=timedelta(minutes=15))),
                _block("ZC1", p=AfterPrev(dur=timedelta(minutes=60))),
            ),
            "circular_chain",
            ["YB1", "ZC1"],
            False,
        ),
        (
            _plan(_block("AA1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=-30)))),
            "negative_duration",
            ["AA1"],
            True,
        ),
    ],
)
def test_every_resolve_failure_reports_its_kind_and_the_blocks_involved(
    plan, kind, handles, has_magnitude
):
    with pytest.raises(PlanViolation) as excinfo:
        plan.resolve()
    violation = excinfo.value.violation
    assert violation.kind.value == kind
    assert [b.h for b in violation.blocks] == handles
    assert violation.message
    assert (violation.magnitude is not None) is has_magnitude


def test_a_violation_survives_a_json_round_trip():
    """The Slack card (#165) receives this over the wire, so every field has
    to serialise — a bare timedelta would not."""
    plan = _plan(
        _block("DW1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=120))),
        _block("DW2", p=FixedStart(st=time(10, 0), dur=timedelta(minutes=60))),
    )
    with pytest.raises(PlanViolation) as excinfo:
        plan.resolve()
    payload = json.dumps(excinfo.value.violation.model_dump(mode="json"))
    assert Violation.model_validate(json.loads(payload)) == excinfo.value.violation
