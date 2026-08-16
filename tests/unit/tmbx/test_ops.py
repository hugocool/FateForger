# tests/unit/tmbx/test_ops.py
from __future__ import annotations

import itertools
from datetime import date, time, timedelta

import pytest

from tmbx.core.models import ET, AfterPrev, Block, FixedStart, Plan
from tmbx.core.ops import (
    AddBlock,
    MoveBlock,
    Patch,
    RemoveBlock,
    UpdateBlock,
    apply_ops,
    validate_patch,
)


def _mint(seq=itertools.count(1)):
    return f"u-new-{next(seq)}"


def _plan():
    return Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="DW1", n="Sprint", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
            Block(uid="u3", h="DW2", n="Review", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=45))),
        ],
    )


def test_remove_by_handle():
    result = apply_ops(_plan(), Patch(ops=[RemoveBlock(h="DW1")]), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "DW2"]


def test_update_merges_only_given_fields():
    result = apply_ops(_plan(), Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]), mint_uid=_mint)
    block = result.by_handle("DW1")
    assert block.n == "Renamed"
    assert block.t is ET.DW
    assert block.uid == "u2"


def test_update_preserves_uid_and_handle():
    result = apply_ops(
        _plan(),
        Patch(ops=[UpdateBlock(h="DW1", p=AfterPrev(dur=timedelta(minutes=30)))]),
        mint_uid=_mint,
    )
    assert result.by_handle("DW1").uid == "u2"


def test_add_after_handle():
    op = AddBlock(after="PR1", h="BU1", n="Buffer", t=ET.BU,
                  p=AfterPrev(dur=timedelta(minutes=10)))
    result = apply_ops(_plan(), Patch(ops=[op]), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "BU1", "DW1", "DW2"]
    assert result.by_handle("BU1").uid.startswith("u-new-")


def test_add_at_end_with_sentinel():
    op = AddBlock(after="END", h="SHU1", n="Shutdown", t=ET.PR,
                  p=AfterPrev(dur=timedelta(minutes=15)))
    result = apply_ops(_plan(), Patch(ops=[op]), mint_uid=_mint)
    assert result.blocks[-1].h == "SHU1"


def test_move_by_anchor():
    result = apply_ops(_plan(), Patch(ops=[MoveBlock(h="DW2", after="PR1")]), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "DW2", "DW1"]


def test_ops_are_commutative():
    """The whole point of set semantics: op order must not change the result."""
    ops = [
        RemoveBlock(h="DW2"),
        UpdateBlock(h="DW1", n="Renamed"),
        AddBlock(after="PR1", h="BU1", n="Buffer", t=ET.BU,
                 p=AfterPrev(dur=timedelta(minutes=10))),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple((b.h, b.n) for b in plan.blocks))
    assert len(results) == 1


def test_two_adds_sharing_an_anchor_are_order_independent():
    """Two Adds anchored on the same handle must not leapfrog each other
    depending on which one the patch lists first — that would make the
    result depend on op order, contradicting set semantics.
    """
    ops = [
        AddBlock(after="PR1", h="AA1", n="A", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
        AddBlock(after="PR1", h="BB1", n="B", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple(b.h for b in plan.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "AA1", "BB1", "DW1", "DW2")


def test_two_moves_sharing_an_anchor_are_order_independent():
    ops = [
        MoveBlock(h="DW1", after="PR1"),
        MoveBlock(h="DW2", after="PR1"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple(b.h for b in plan.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "DW1", "DW2")


def test_two_prepends_are_order_independent():
    ops = [
        AddBlock(after=None, h="AA1", n="A", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
        AddBlock(after=None, h="BB1", n="B", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple(b.h for b in plan.blocks))
    assert len(results) == 1
    (order,) = results
    assert order[:2] == ("AA1", "BB1")


def test_validate_rejects_unknown_handle():
    errors = validate_patch(_plan(), Patch(ops=[RemoveBlock(h="NOPE1")]))
    assert any("NOPE1" in e for e in errors)


def test_validate_rejects_two_ops_touching_one_block():
    errors = validate_patch(
        _plan(), Patch(ops=[UpdateBlock(h="DW1", n="A"), RemoveBlock(h="DW1")])
    )
    assert any("DW1" in e for e in errors)


def test_validate_rejects_duplicate_new_handle():
    op = AddBlock(after="END", h="DW1", n="Clash", t=ET.DW,
                  p=AfterPrev(dur=timedelta(minutes=30)))
    assert any("DW1" in e for e in validate_patch(_plan(), Patch(ops=[op])))


def test_validate_requires_anchor_source_for_fixed_timing():
    op = AddBlock(after="END", h="APP1", n="Appointment", t=ET.M,
                  p=FixedStart(st=time(16, 0), dur=timedelta(minutes=30)))
    assert any("anchor_source" in e for e in validate_patch(_plan(), Patch(ops=[op])))


def test_apply_raises_on_invalid_patch():
    with pytest.raises(ValueError):
        apply_ops(_plan(), Patch(ops=[RemoveBlock(h="NOPE1")]), mint_uid=_mint)


# --- Fix round 1: false negatives (validated patch that still raises) -----


def test_validate_catches_bg_retimed_without_fixed_timing():
    """UpdateBlock only changes t to BG; p stays ap. Block forbids BG+ap."""
    errors = validate_patch(_plan(), Patch(ops=[UpdateBlock(h="DW1", t=ET.BG)]))
    assert any("BG" in e for e in errors)


def test_apply_raises_clean_error_not_raw_pydantic_for_bg_update():
    """validate_patch catching it means apply_ops's own guard fires first —
    never a bare pydantic.ValidationError escaping from Block construction.
    """
    with pytest.raises(ValueError) as excinfo:
        apply_ops(_plan(), Patch(ops=[UpdateBlock(h="DW1", t=ET.BG)]), mint_uid=_mint)
    assert str(excinfo.value).startswith("invalid patch:")


def test_validate_catches_bg_add_without_fixed_timing():
    op = AddBlock(after="END", h="BG1", n="Music", t=ET.BG,
                  p=AfterPrev(dur=timedelta(minutes=30)))
    errors = validate_patch(_plan(), Patch(ops=[op]))
    assert any("BG" in e for e in errors)


def test_validate_catches_malformed_new_handle():
    op = AddBlock(after="END", h="bad-handle!", n="X", t=ET.DW,
                  p=AfterPrev(dur=timedelta(minutes=10)))
    errors = validate_patch(_plan(), Patch(ops=[op]))
    assert any("bad-handle!" in e for e in errors)


# --- Fix round 1: false positive (valid patch wrongly rejected) -----------


def test_validate_does_not_reject_retiming_an_already_anchored_block():
    """PR1 already carries anchor_source='user'; UpdateBlock's own docstring
    says unset fields are untouched, so the merge preserves it and the
    result is a valid Block. Restating anchor_source should not be required.
    """
    op = UpdateBlock(h="PR1", p=FixedStart(st=time(10, 0), dur=timedelta(minutes=30)))
    assert validate_patch(_plan(), Patch(ops=[op])) == []
    result = apply_ops(_plan(), Patch(ops=[op]), mint_uid=_mint)
    assert result.by_handle("PR1").anchor_source == "user"
    assert result.by_handle("PR1").p.st == time(10, 0)


# --- Fix round 1: anchor removed in the same patch (replace-in-place) -----


def _plan_multi():
    """PR1 and DW3 both fs-anchored, so removing either alone still leaves
    the chain anchored — isolates the anchor-resolution fix from the
    unrelated (and out of scope) chain-anchor invariant.
    """
    return Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="DW1", n="Sprint", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
            Block(uid="u3", h="DW2", n="Review", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=45))),
            Block(uid="u4", h="DW3", n="Wrap", t=ET.DW,
                  p=FixedStart(st=time(15, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
        ],
    )


def test_add_after_a_removed_anchor_lands_where_the_anchor_was():
    """Replace-in-place: remove a block and add its replacement anchored on
    the handle just removed — the idiomatic way a model would express a
    swap. The pre-patch position of the removed handle is still real.
    """
    result = apply_ops(
        _plan(),
        Patch(ops=[
            RemoveBlock(h="DW1"),
            AddBlock(after="DW1", h="BU1", n="Buffer", t=ET.BU,
                     p=AfterPrev(dur=timedelta(minutes=10))),
        ]),
        mint_uid=_mint,
    )
    assert [b.h for b in result.blocks] == ["PR1", "BU1", "DW2"]


def test_add_after_a_removed_anchor_walks_back_past_other_removed_blocks():
    """DW1 and DW2 both removed; the add anchors on DW2. Its predecessor
    DW1 is also gone, so resolution must keep walking back to PR1 — proof
    the position tracks the pre-patch order, not just one hop.
    """
    result = apply_ops(
        _plan_multi(),
        Patch(ops=[
            RemoveBlock(h="DW1"),
            RemoveBlock(h="DW2"),
            AddBlock(after="DW2", h="BU1", n="Buffer", t=ET.BU,
                     p=AfterPrev(dur=timedelta(minutes=10))),
        ]),
        mint_uid=_mint,
    )
    assert [b.h for b in result.blocks] == ["PR1", "BU1", "DW3"]


def test_add_after_a_removed_first_anchor_prepends():
    """PR1 was first in pre-patch order; removing it leaves nothing before
    it to walk back to, so the replacement prepends.
    """
    result = apply_ops(
        _plan_multi(),
        Patch(ops=[
            RemoveBlock(h="PR1"),
            AddBlock(after="PR1", h="BU1", n="Buffer", t=ET.BU,
                     p=AfterPrev(dur=timedelta(minutes=10))),
        ]),
        mint_uid=_mint,
    )
    assert [b.h for b in result.blocks] == ["BU1", "DW1", "DW2", "DW3"]


# --- Cross-type same-anchor interactions (pinning reviewer-verified cases) -


def test_add_and_move_sharing_an_anchor_are_order_independent():
    ops = [
        AddBlock(after="PR1", h="AA1", n="A", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
        MoveBlock(h="DW2", after="PR1"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple(b.h for b in plan.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "AA1", "DW2", "DW1")


def test_add_anchored_on_a_block_moved_in_the_same_patch():
    """The move already resolves DW2 to its new position before the add
    phase runs, so 'after DW2' lands there — not at DW2's old spot.
    """
    ops = [
        AddBlock(after="DW2", h="AA1", n="A", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
        MoveBlock(h="DW2", after="PR1"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple(b.h for b in plan.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "DW2", "AA1", "DW1")


# --- Fix round 2, Finding 1: chained reorders (moves anchored on moves) ---


def _plan_four():
    return Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="DW1", n="Sprint", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
            Block(uid="u3", h="DW2", n="Review", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=45))),
            Block(uid="u4", h="DW3", n="Wrap", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
        ],
    )


def test_chained_reorder_of_three_blocks_is_order_independent():
    """PR1, DW1, DW2, DW3 -> move DW2 right after PR1, then move DW1 right
    after DW2's NEW spot. The natural way to express reordering DW1 and
    DW2: intended result PR1, DW2, DW1, DW3 — not a relabeling of the
    pre-patch order.
    """
    ops = [
        MoveBlock(h="DW2", after="PR1"),
        MoveBlock(h="DW1", after="DW2"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan_four(), Patch(ops=list(permutation)), mint_uid=lambda: "u-x")
        results.add(tuple(b.h for b in plan.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "DW2", "DW1", "DW3")


def test_move_anchored_on_a_co_moved_block_that_is_anchored_on_a_removed_block():
    """BB1 moves to where the removed XX1 used to be; AA1 moves to right
    after BB1's NEW spot. Two dependency mechanisms (removed-anchor
    walk-back and co-moved-anchor deferral) composing in one patch.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="AA1", n="A", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u3", h="BB1", n="B", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u4", h="XX1", n="X", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
        ],
    )
    ops = [
        RemoveBlock(h="XX1"),
        MoveBlock(h="BB1", after="XX1"),
        MoveBlock(h="AA1", after="BB1"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        result = apply_ops(plan, Patch(ops=list(permutation)), mint_uid=lambda: "u-x")
        results.add(tuple(b.h for b in result.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "BB1", "AA1")


def test_validate_rejects_cyclic_move_anchors():
    errors = validate_patch(
        _plan(), Patch(ops=[MoveBlock(h="DW1", after="DW2"), MoveBlock(h="DW2", after="DW1")])
    )
    assert any("DW1" in e and "DW2" in e for e in errors)


def test_apply_raises_clean_error_for_cyclic_move_anchors():
    with pytest.raises(ValueError) as excinfo:
        apply_ops(
            _plan(),
            Patch(ops=[MoveBlock(h="DW1", after="DW2"), MoveBlock(h="DW2", after="DW1")]),
            mint_uid=_mint,
        )
    assert str(excinfo.value).startswith("invalid patch:")


# --- Fix round 2, Finding 2: validate_patch must catch Plan-level gaps ----


def test_validate_catches_chain_left_without_an_anchor_by_removal():
    """PR1 is the plan's only fs-anchored block; removing it leaves the
    whole chain unanchored — a Plan-level invariant, not a Block-level one.
    """
    errors = validate_patch(_plan(), Patch(ops=[RemoveBlock(h="PR1")]))
    assert any("anchor" in e for e in errors)


def test_apply_raises_clean_error_not_raw_pydantic_for_unanchored_chain():
    with pytest.raises(ValueError) as excinfo:
        apply_ops(_plan(), Patch(ops=[RemoveBlock(h="PR1")]), mint_uid=_mint)
    assert str(excinfo.value).startswith("invalid patch:")


def test_validate_catches_chain_left_unanchored_by_a_retime():
    """Retiming the only fs-anchored block away from fs/fw unanchors the
    chain just as surely as removing it outright."""
    errors = validate_patch(
        _plan(), Patch(ops=[UpdateBlock(h="PR1", p=AfterPrev(dur=timedelta(minutes=30)))])
    )
    assert any("anchor" in e for e in errors)


# --- Fix round 2, Finding 3: reusing a handle freed in the same patch -----


def test_validate_allows_reusing_a_handle_freed_by_removal_in_the_same_patch():
    op = AddBlock(after="END", h="DW1", n="New Sprint", t=ET.DW,
                  p=AfterPrev(dur=timedelta(minutes=20)))
    errors = validate_patch(_plan(), Patch(ops=[RemoveBlock(h="DW1"), op]))
    assert errors == []


def test_add_reuses_a_handle_freed_by_removal_in_the_same_patch():
    """Replace in place, keeping the name — as idiomatic as reusing the
    position, which fix round 1 already allows."""
    result = apply_ops(
        _plan(),
        Patch(ops=[
            RemoveBlock(h="DW1"),
            AddBlock(after="END", h="DW1", n="New Sprint", t=ET.DW,
                     p=AfterPrev(dur=timedelta(minutes=20))),
        ]),
        mint_uid=_mint,
    )
    new_dw1 = result.by_handle("DW1")
    assert new_dw1.n == "New Sprint"
    assert new_dw1.uid.startswith("u-new-")


def test_validate_still_rejects_a_handle_added_while_it_survives():
    """Regression guard: reuse is legal only when the original is removed —
    adding a handle that still stands must still be rejected.
    """
    op = AddBlock(after="END", h="DW1", n="Clash", t=ET.DW,
                  p=AfterPrev(dur=timedelta(minutes=30)))
    assert any("DW1" in e for e in validate_patch(_plan(), Patch(ops=[op])))


# --- Fix round 3: walk-back through a removed anchor can cross a co-moved
# block that no literal `after` reference ever named -----------------------


def test_walk_back_through_a_removed_anchor_crosses_a_co_moved_block():
    """PR1, MM1, RR1, NN1. MM1 moves to END; NN1 anchors on removed RR1,
    whose real pre-patch predecessor is MM1 — not named by any literal
    `after`, only reachable by the walk-back itself. MM1 must place first.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="MM1", n="M", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u3", h="RR1", n="R", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u4", h="NN1", n="N", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
        ],
    )
    ops = [
        RemoveBlock(h="RR1"),
        MoveBlock(h="MM1", after="END"),
        MoveBlock(h="NN1", after="RR1"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        result = apply_ops(plan, Patch(ops=list(permutation)), mint_uid=lambda: "u-x")
        results.add(tuple(b.h for b in result.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "MM1", "NN1")


def test_walk_back_crosses_two_co_moved_blocks_in_a_dependency_chain():
    """PR1, MM1, RG1, KK1, RR1, NN1. RG1 and RR1 removed. MM1 -> END
    (trivial). KK1 -> RG1 (walk-back finds co-moved MM1 — not literal).
    NN1 -> RR1 (walk-back finds co-moved KK1 — not literal). NN1's real
    resolution transitively crosses both MM1 and KK1 before it can place.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="MM1", n="M", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u3", h="RG1", n="RG", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u4", h="KK1", n="K", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u5", h="RR1", n="R", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u6", h="NN1", n="N", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
        ],
    )
    ops = [
        RemoveBlock(h="RG1"),
        RemoveBlock(h="RR1"),
        MoveBlock(h="MM1", after="END"),
        MoveBlock(h="KK1", after="RG1"),
        MoveBlock(h="NN1", after="RR1"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        result = apply_ops(plan, Patch(ops=list(permutation)), mint_uid=lambda: "u-x")
        results.add(tuple(b.h for b in result.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "MM1", "KK1", "NN1")


def test_walk_back_crosses_a_co_moved_block_anchored_on_another_removed_handle():
    """PR1, RG1, ZK1, RR1, AN1. RG1 and RR1 both removed. ZK1 -> RG1
    (walk-back lands directly on stable PR1 — ZK1 has no dependency of its
    own). AN1 -> RR1 (walk-back finds co-moved ZK1). Two DIFFERENT removed
    handles, each resolved by its own walk-back, chained through one
    co-moved block.

    Handles are deliberately chosen so alphabetical tie-break would land
    them in the WRONG order (AN1 before ZK1) if AN1 were incorrectly
    treated as having no dependency and batched alongside ZK1 in the same
    round — the exact shape of the bug this pins.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="RG1", n="RG", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u3", h="ZK1", n="Z", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u4", h="RR1", n="R", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u5", h="AN1", n="A", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
        ],
    )
    ops = [
        RemoveBlock(h="RG1"),
        RemoveBlock(h="RR1"),
        MoveBlock(h="ZK1", after="RG1"),
        MoveBlock(h="AN1", after="RR1"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        result = apply_ops(plan, Patch(ops=list(permutation)), mint_uid=lambda: "u-x")
        results.add(tuple(b.h for b in result.blocks))
    assert len(results) == 1
    (order,) = results
    assert order == ("PR1", "ZK1", "AN1")


def test_validate_rejects_a_cycle_composed_via_walk_back():
    """XX1 anchors on removed ZZ1, whose walk-back predecessor is co-moved
    YY1 (no literal reference between XX1 and YY1 at all). YY1 anchors
    directly on XX1. The two moves cycle through each other only via the
    walk-back path — validate_patch must still catch it.
    """
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="XX1", n="X", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u3", h="YY1", n="Y", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
            Block(uid="u4", h="ZZ1", n="Z", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=30))),
        ],
    )
    ops = [
        RemoveBlock(h="ZZ1"),
        MoveBlock(h="XX1", after="ZZ1"),
        MoveBlock(h="YY1", after="XX1"),
    ]
    errors = validate_patch(plan, Patch(ops=ops))
    assert any("XX1" in e and "YY1" in e for e in errors)
    with pytest.raises(ValueError) as excinfo:
        apply_ops(plan, Patch(ops=ops), mint_uid=_mint)
    assert str(excinfo.value).startswith("invalid patch:")
