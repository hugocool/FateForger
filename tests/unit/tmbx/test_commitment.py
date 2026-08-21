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


def test_three_chained_redundant_anchors_are_all_flagged():
    """Each of DW1/DW2/DW3 independently lands exactly where ap would place
    it given its immediate predecessor — over-specification isn't limited
    to a single redundant pin."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 30), dur=timedelta(minutes=60)), anchor_source="user"),
        Block(uid="u3", h="DW2", n="More", t=ET.DW,
              p=FixedStart(st=time(10, 30), dur=timedelta(minutes=45)), anchor_source="user"),
        Block(uid="u4", h="DW3", n="Even more", t=ET.DW,
              p=FixedStart(st=time(11, 15), dur=timedelta(minutes=30)), anchor_source="user"),
    ])
    assert overspecified(plan) == ["DW1", "DW2", "DW3"]


def test_empty_plan_returns_empty_list():
    """No blocks, nothing to flag — and nothing to raise on either."""
    plan = _p(blocks=[])
    assert overspecified(plan) == []


def test_all_background_plan_returns_empty_list():
    """A plan with only BG blocks has an empty chain; every block is
    skipped by the BG guard before it ever reaches candidacy."""
    plan = _p(blocks=[
        Block(uid="u1", h="BG1", n="Focus block", t=ET.BG,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="calendar"),
        Block(uid="u2", h="BG2", n="Do not disturb", t=ET.BG,
              p=FixedWindow(st=time(12, 0), et=time(13, 0)), anchor_source="calendar"),
    ])
    assert overspecified(plan) == []


def test_circular_bn_ap_chain_in_the_baseline_returns_empty_list():
    """DW0 (bn) is immediately followed by DW1 (ap) in the *original* plan
    — resolve()'s own ap-cannot-follow-an-unresolved-bn guard fires on the
    very first (baseline) resolve, before any candidate is even built. This
    exercises the outer ``except ValueError: return []`` directly."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW0", n="Wait", t=ET.DW, p=BeforeNext(dur=timedelta(minutes=15))),
        Block(uid="u3", h="DW1", n="Work", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=60))),
    ])
    assert overspecified(plan) == []


def test_fixed_block_immediately_after_bn_is_never_flagged():
    """Documents the known conservative blind spot: DW1 is fixed at 09:30,
    exactly where a naive ap-from-PR1 computation would land it — it looks
    redundant — but it is immediately preceded by DW0 (bn). Relaxing DW1 to
    ap trips resolve()'s ap-cannot-follow-an-unresolved-bn guard on the
    *candidate* resolve, so the probe can't confirm the match and silently
    declines rather than flagging it. This also exercises the inner
    ``except ValueError: continue`` directly."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW0", n="Wait", t=ET.DW, p=BeforeNext(dur=timedelta(minutes=15))),
        Block(uid="u3", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 30), dur=timedelta(minutes=60)), anchor_source="user"),
    ])
    assert overspecified(plan) == []


# ---------------------------------------------------------------------------
# A pin can be the only thing enforcing a constraint. Flagging it tells the
# model to unpin the boundary and call it hygiene — measured under a joint
# tmbx + constraint-memory session (docs/superpowers/plans/
# 2026-08-21-memory-integration-report.md, finding 2), where 2 of 4 runs
# relaxed a MUST-backed bedtime pin "to prevent overspecification".
# ---------------------------------------------------------------------------


def test_a_constraint_anchored_pin_is_not_flagged_but_its_twin_is():
    """The whole fix, as one pair: two plans identical in every field
    except ``anchor_source``. The ``user``-anchored pin at 09:30 is a
    gratuitous pin and stays flagged; the ``constraint``-anchored one is
    the boundary being enforced and must not be.

    Written as a single test on purpose — the two halves are only
    meaningful together, and splitting them lets one drift green while the
    other silently stops discriminating.
    """

    def _plan_with(anchor_source):
        return _p(blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="DW1", n="Work", t=ET.DW,
                  p=FixedStart(st=time(9, 30), dur=timedelta(minutes=90)),
                  anchor_source=anchor_source),
        ])

    assert overspecified(_plan_with("user")) == ["DW1"]
    assert overspecified(_plan_with("constraint")) == []


def test_suppression_is_scoped_to_constraint_and_does_not_leak():
    """Three redundant pins, one per ``anchor_source`` value. Only the
    constraint-anchored one is suppressed: ``user`` is the default filler
    this codebase reaches for whenever ``Block`` demands a source, and
    ``calendar`` is an observed fact rather than an assertion of intent —
    treating either as deliberate would suppress essentially every pin and
    delete the check rather than fix it.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="User pin", t=ET.DW,
              p=FixedStart(st=time(9, 30), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u3", h="DW2", n="Constraint pin", t=ET.DW,
              p=FixedStart(st=time(10, 0), dur=timedelta(minutes=30)),
              anchor_source="constraint"),
        Block(uid="u4", h="DW3", n="Calendar pin", t=ET.DW,
              p=FixedStart(st=time(10, 30), dur=timedelta(minutes=30)),
              anchor_source="calendar"),
    ])
    assert overspecified(plan) == ["DW1", "DW3"]


def test_the_bedtime_pin_from_the_joint_session_is_not_flagged():
    """The measured case, verbatim: gym pushed to 18:30, wind-down
    shortened to absorb it, bedtime pinned 22:00-23:00 because a MUST
    sleep-at-23:00 constraint says so. Relaxing BED1 to ``ap`` changes no
    resolved time *today* — which is exactly why it looked gratuitous —
    but it is the only thing in the plan holding the boundary, and the
    next edit that grows WIND1 would push bedtime past 23:00 with nothing
    to refuse.
    """
    plan = _p(blocks=[
        Block(uid="u1", h="GYM1", n="Gym", t=ET.H,
              p=FixedStart(st=time(18, 30), dur=timedelta(hours=1)), anchor_source="user"),
        Block(uid="u2", h="WIND1", n="Wind down", t=ET.R,
              p=AfterPrev(dur=timedelta(hours=2, minutes=30))),
        Block(uid="u3", h="BED1", n="Bedtime", t=ET.R,
              p=FixedWindow(st=time(22, 0), et=time(23, 0)), anchor_source="constraint"),
    ])
    assert overspecified(plan) == []


def test_a_constraint_anchored_fixed_window_is_not_flagged_either():
    """Suppression keys on the source, not on which fixed variant carries
    it — ``fw`` pins are flagged in their own right (see
    ``test_redundant_fixed_window_is_flagged``) and must be suppressed on
    the same terms as ``fs``."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedWindow(st=time(9, 30), et=time(11, 0)), anchor_source="constraint"),
    ])
    assert overspecified(plan) == []
