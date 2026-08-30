# tests/unit/tmbx/test_ops.py
from __future__ import annotations

import itertools
from datetime import date, time, timedelta

import pytest

from tmbx.core.models import (
    ET,
    AfterPrev,
    BeforeNext,
    Block,
    FixedStart,
    FixedWindow,
    Plan,
    PlanViolation,
    ViolationKind,
)
from tmbx.core.ops import (
    AddBlock,
    MoveBlock,
    Patch,
    RemoveBlock,
    UpdateBlock,
    apply_ops,
    validate_patch,
)


def test_add_after_schema_teaches_that_omitting_it_follows_the_previous_add():
    """This description is the only documentation a model ever reads about
    `after`. It used to say the anchor "cannot reference a handle created
    in the same patch"; the model believed it, could not express a chain,
    and pinned a whole day to wall-clock times instead (journal entry 134).
    The absence assertions are the load-bearing half — a stale sentence
    left beside a true one is still read.

    Two sentences are now stale, not one. "Use None for fixed-start
    additions unless ordering against another block matters" was advice
    about a default that no longer exists: a fixed-start add omitting
    `after` follows the previous add like any other, and pinning its start
    says nothing about where it sits in the list.
    """
    description = AddBlock.model_json_schema()["properties"]["after"][
        "description"
    ].lower()

    assert "omit" in description
    assert "listed before it" in description
    assert "override" in description
    assert "same patch" in description
    assert "dependency order" in description
    assert "cycle" in description
    assert "cannot reference a handle created in the same patch" not in description
    assert "use none for fixed-start additions" not in description


def test_after_defaults_to_the_prev_sentinel_so_omission_survives_serialisation():
    """The journal stores ``patch.model_dump_json()``. If "omitted" lived
    only in ``model_fields_set``, that dump would write ``after: null`` —
    which means *prepend* — into the record of a patch that meant *follow
    the one before*, and the record of what the planner did would disagree
    with what the planner did. A sentinel value round-trips; parse
    metadata does not.
    """
    parsed = Patch.model_validate(
        {"ops": [{"op": "add", "h": "AA1", "n": "A", "t": "BU",
                  "p": {"a": "ap", "dur": "PT5M"}}]}
    )
    assert parsed.ops[0].after == "PREV"

    round_tripped = Patch.model_validate_json(parsed.model_dump_json())
    assert round_tripped.ops[0].after == parsed.ops[0].after

    plan = _plan()
    assert [b.h for b in apply_ops(plan, parsed, mint_uid=lambda: "u").blocks] == [
        b.h for b in apply_ops(plan, round_tripped, mint_uid=lambda: "u").blocks
    ]


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


def test_two_adds_sharing_an_anchor_keep_the_order_the_patch_listed():
    """Two adds anchored on the same handle land in the order they were
    written. Under set semantics they landed alphabetically instead, and
    handles chosen to make that visible are the whole point here: BB1
    listed first must come out first, which handle order alone would never
    produce.

    Still one insert pass, not two — the two adds do not leapfrog each
    other, they are placed together and ranked. What changed is the rank.
    """
    listed_bb_first = [
        AddBlock(after="PR1", h="BB1", n="B", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
        AddBlock(after="PR1", h="AA1", n="A", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
    ]
    forward = apply_ops(_plan(), Patch(ops=listed_bb_first), mint_uid=lambda: "u-fixed")
    reverse = apply_ops(
        _plan(), Patch(ops=list(reversed(listed_bb_first))), mint_uid=lambda: "u-fixed"
    )

    assert [b.h for b in forward.blocks] == ["PR1", "BB1", "AA1", "DW1", "DW2"]
    assert [b.h for b in reverse.blocks] == ["PR1", "AA1", "BB1", "DW1", "DW2"]


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


def test_two_explicit_prepends_keep_the_order_the_patch_listed():
    """Two adds that both say `after: null` mean it — neither is chaining
    off the other, both go to the front — and the front is still a
    position two blocks can share, so the listed order decides which is
    first.
    """
    ops = [
        AddBlock(after=None, h="BB1", n="B", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
        AddBlock(after=None, h="AA1", n="A", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
    ]
    forward = apply_ops(_plan(), Patch(ops=ops), mint_uid=lambda: "u-fixed")
    reverse = apply_ops(
        _plan(), Patch(ops=list(reversed(ops))), mint_uid=lambda: "u-fixed"
    )
    assert [b.h for b in forward.blocks][:2] == ["BB1", "AA1"]
    assert [b.h for b in reverse.blocks][:2] == ["AA1", "BB1"]


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


# ---------------------------------------------------------------------------
# Relaxing a constraint-anchored pin destroys the record of why the boundary
# existed. Refused here, at validate_patch, so both the preview and the
# commit path see the same answer -- and so the refusal surfaces as the
# existing "invalid_patch" reason code rather than a sixth one.
# ---------------------------------------------------------------------------


def _plan_with_bedtime(anchor_source="constraint"):
    """The joint-session day: a wind-down that flexes, then a bedtime pin
    the sleep-at-23:00 MUST is holding."""
    return Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="GYM1", n="Gym", t=ET.H,
                  p=FixedStart(st=time(18, 30), dur=timedelta(hours=1)),
                  anchor_source="user"),
            Block(uid="u2", h="WIND1", n="Wind down", t=ET.R,
                  p=AfterPrev(dur=timedelta(hours=2, minutes=30))),
            Block(uid="u3", h="BED1", n="Bedtime", t=ET.R,
                  p=FixedWindow(st=time(22, 0), et=time(23, 0)),
                  anchor_source=anchor_source),
        ],
    )


def test_relaxing_a_constraint_anchored_pin_is_refused():
    op = UpdateBlock(h="BED1", p=AfterPrev(dur=timedelta(hours=1)),
                     why="Relax BED1 to ap mode to prevent overspecification")
    errors = validate_patch(_plan_with_bedtime(), Patch(ops=[op]))
    assert any("BED1" in e for e in errors)


def test_relaxing_a_constraint_anchored_pin_raises_from_apply_ops():
    """apply_ops turns any validate_patch error into the ValueError the
    server already renders as reason "invalid_patch" — no new refusal
    path, no new reason code."""
    op = UpdateBlock(h="BED1", p=AfterPrev(dur=timedelta(hours=1)))
    with pytest.raises(ValueError, match="invalid patch"):
        apply_ops(_plan_with_bedtime(), Patch(ops=[op]), mint_uid=_mint)


def test_relaxing_a_user_anchored_pin_is_allowed():
    """The same op against the same block, differing only in
    ``anchor_source``. A user can change their mind mid-conversation and
    is right there to say so; a standing constraint is not."""
    op = UpdateBlock(h="BED1", p=AfterPrev(dur=timedelta(hours=1)))
    assert validate_patch(_plan_with_bedtime("user"), Patch(ops=[op])) == []


def test_relaxing_a_calendar_anchored_pin_is_allowed():
    op = UpdateBlock(h="BED1", p=AfterPrev(dur=timedelta(hours=1)))
    assert validate_patch(_plan_with_bedtime("calendar"), Patch(ops=[op])) == []


def test_retiming_a_constraint_anchored_pin_within_fixed_modes_is_allowed():
    """fw -> fs is still a pin; the boundary survives. Only leaving fixed
    timing altogether drops it, so only that is refused."""
    op = UpdateBlock(h="BED1", p=FixedStart(st=time(22, 30), dur=timedelta(minutes=30)),
                     why="constraint moved to 23:00 sharp")
    assert validate_patch(_plan_with_bedtime(), Patch(ops=[op])) == []


def test_a_constraint_pin_can_be_unpinned_by_re_sourcing_it_first():
    """The escape hatch, and it is deliberately two patches: an op may
    only touch a handle once, so re-sourcing and relaxing cannot be
    smuggled into the same patch. Whoever drops the boundary has to say so
    in its own op, with its own ``why``, and it lands in the journal as
    two entries rather than one.
    """
    re_source = UpdateBlock(h="BED1", anchor_source="user",
                            why="user said tonight is a late one")
    plan = _plan_with_bedtime()
    assert validate_patch(plan, Patch(ops=[re_source])) == []
    re_sourced = apply_ops(plan, Patch(ops=[re_source]), mint_uid=_mint)
    assert re_sourced.by_handle("BED1").anchor_source == "user"

    relax = UpdateBlock(h="BED1", p=AfterPrev(dur=timedelta(hours=1)))
    assert validate_patch(re_sourced, Patch(ops=[relax])) == []


def test_relaxing_to_before_next_is_refused_too():
    """``bn`` is a duration with no pinned edge, exactly like ``ap`` — the
    rule is "leaves fixed timing", not "becomes ap"."""
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="GYM1", n="Gym", t=ET.H,
                  p=FixedStart(st=time(18, 30), dur=timedelta(hours=1)),
                  anchor_source="user"),
            Block(uid="u2", h="BED1", n="Bedtime", t=ET.R,
                  p=FixedWindow(st=time(22, 0), et=time(23, 0)),
                  anchor_source="constraint"),
            Block(uid="u3", h="SLP1", n="Asleep", t=ET.R,
                  p=FixedStart(st=time(23, 0), dur=timedelta(hours=8)),
                  anchor_source="constraint"),
        ],
    )
    op = UpdateBlock(h="BED1", p=BeforeNext(dur=timedelta(hours=1)))
    assert any("BED1" in e for e in validate_patch(plan, Patch(ops=[op])))


def test_an_update_that_does_not_touch_timing_is_never_refused():
    """Renaming a constraint-anchored block leaves the pin exactly where
    it was — ``UpdateBlock``'s unset fields are untouched, so an unset
    ``p`` is not a relaxation and must not be read as one."""
    op = UpdateBlock(h="BED1", n="Bed")
    assert validate_patch(_plan_with_bedtime(), Patch(ops=[op])) == []


def test_removing_a_constraint_anchored_block_is_not_refused():
    """Deleting a block is a different act from quietly unpinning one: it
    is visible in the rendered plan on the very next turn, where an
    unpinned block looks identical to a pinned one. Documented, not
    accidental — narrowing the rule to relaxation is what keeps its
    refusal message honest."""
    plan = _plan_with_bedtime()
    assert validate_patch(plan, Patch(ops=[RemoveBlock(h="BED1")])) == []


# ---------------------------------------------------------------------------
# An add's anchor may name a handle the same patch adds.
#
# Measured live on 2026-08-30 (tmbx journal entry 133, plan_date 2026-08-31):
# the planner built a day the way least commitment asks for it -- one real
# anchor, everything else `ap` and chained off the block before it -- and tmbx
# refused all thirteen relative ops with "anchor MR1 not found; op 3: anchor
# DW1 not found; ...". Four seconds later (entry 134) the model gave up and
# pinned all fourteen blocks to wall-clock times: a day that cannot shift, so
# every downstream buffer and constraint rule quietly stops applying. That is
# the plan `commitment.overspecified()` exists to call a mistake, and the
# anchor rule was what produced it.
#
# A patch is still a set. What changed is which handles an anchor may name:
# the pre-patch plan OR this patch's own adds, with adds applied in dependency
# order so the anchor exists before the block naming it. Order still comes from
# the dependency graph, never from where an op sat in the list.
# ---------------------------------------------------------------------------


def _add(after, h, n="X", t=ET.BU, minutes=30):
    return AddBlock(after=after, h=h, n=n, t=t, p=AfterPrev(dur=timedelta(minutes=minutes)))


def test_add_anchored_on_another_add_in_the_same_patch():
    """Two ops, one chain. Handles chosen so the alphabetical tie-break
    inside ``_insert_batch`` would put them the WRONG way round if both
    were (incorrectly) treated as anchored on PR1: ZZ1 must precede AA1
    only because AA1 names it.
    """
    ops = [_add("PR1", "ZZ1"), _add("ZZ1", "AA1")]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "ZZ1", "AA1", "DW1", "DW2"]


def test_a_chain_of_adds_resolves_in_dependency_order_not_list_order():
    """Five adds naming each other, listed in an order alphabetical
    tie-breaking cannot reproduce."""
    ops = [
        _add("PR1", "MM1"),
        _add("MM1", "BB1"),
        _add("BB1", "TT1"),
        _add("TT1", "AA1"),
        _add("AA1", "NN1"),
    ]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == [
        "PR1", "MM1", "BB1", "TT1", "AA1", "NN1", "DW1", "DW2",
    ]


def test_a_chain_given_in_reverse_order_resolves_identically():
    """The anchor op listed AFTER the op naming it. If ordering came from
    the op list rather than the dependency graph, this is where it would
    show."""
    forward = [
        _add("PR1", "MM1"),
        _add("MM1", "BB1"),
        _add("BB1", "TT1"),
        _add("TT1", "AA1"),
    ]
    reverse = list(reversed(forward))

    a = apply_ops(_plan(), Patch(ops=forward), mint_uid=lambda: "u-fixed")
    b = apply_ops(_plan(), Patch(ops=reverse), mint_uid=lambda: "u-fixed")

    assert [x.h for x in a.blocks] == ["PR1", "MM1", "BB1", "TT1", "AA1", "DW1", "DW2"]
    assert [x.h for x in b.blocks] == [x.h for x in a.blocks]


def test_a_same_patch_chain_can_be_rooted_on_a_pre_patch_block():
    """The root of the chain is an existing handle, not a sentinel — the
    shape the live planner used to hang a day off a real calendar event."""
    ops = [_add("DW1", "ZZ1"), _add("ZZ1", "AA1")]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "DW1", "ZZ1", "AA1", "DW2"]


def test_a_same_patch_chain_rooted_on_a_handle_removed_by_the_same_patch():
    """Replace-in-place, chained: the removed handle still names a real
    pre-patch position, and the chain hanging off it follows it there."""
    ops = [
        RemoveBlock(h="DW1"),
        _add("DW1", "ZZ1"),
        _add("ZZ1", "AA1"),
    ]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "ZZ1", "AA1", "DW2"]


def test_an_anchor_naming_a_reused_handle_means_the_block_the_patch_leaves():
    """DW1 is removed and re-added in one patch, and a third op anchors on
    DW1. A handle names one block once the patch has landed, so it names
    the new one — not the ghost of the position the old one held.
    """
    ops = [
        RemoveBlock(h="DW1"),
        AddBlock(after="END", h="DW1", n="New Sprint", t=ET.DW,
                 p=AfterPrev(dur=timedelta(minutes=20))),
        _add("DW1", "AA1"),
    ]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "DW2", "DW1", "AA1"]


def test_an_anchor_that_names_nothing_is_still_refused():
    errors = validate_patch(_plan(), Patch(ops=[_add("NOPE1", "AA1")]))
    assert any("NOPE1" in e and "not found" in e for e in errors)


def test_a_same_patch_chain_is_deterministic_across_applications():
    """Two applications of one patch must produce one plan. Nothing in the
    layering may read a set's iteration order."""
    ops = [
        _add("PR1", "MM1"),
        _add("MM1", "BB1"),
        _add("PR1", "TT1"),
        _add("BB1", "AA1"),
        _add("TT1", "NN1"),
    ]
    first = apply_ops(_plan(), Patch(ops=ops), mint_uid=lambda: "u-fixed")
    second = apply_ops(_plan(), Patch(ops=ops), mint_uid=lambda: "u-fixed")
    assert [b.h for b in first.blocks] == [b.h for b in second.blocks]
    assert [b.h for b in first.blocks] == [
        "PR1", "MM1", "BB1", "AA1", "TT1", "NN1", "DW1", "DW2",
    ]


# --- a chain that closes on itself has no order at all --------------------


def test_validate_rejects_cyclic_add_anchors():
    """Two adds naming each other. Neither can be placed first, so the
    patch describes no plan — refused in the same shape as a cyclic move,
    because it is the same defect one phase over."""
    ops = [_add("AA1", "ZZ1"), _add("ZZ1", "AA1")]
    errors = validate_patch(_plan(), Patch(ops=ops))
    assert any("cyclic add anchors" in e and "AA1" in e and "ZZ1" in e for e in errors)


def test_apply_raises_a_clean_error_for_cyclic_add_anchors():
    ops = [_add("AA1", "ZZ1"), _add("ZZ1", "AA1")]
    with pytest.raises(ValueError) as excinfo:
        apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert str(excinfo.value).startswith("invalid patch:")
    assert "cyclic add anchors" in str(excinfo.value)


def test_validate_rejects_an_add_anchored_on_itself():
    errors = validate_patch(_plan(), Patch(ops=[_add("AA1", "AA1")]))
    assert any("cyclic add anchors" in e and "AA1" in e for e in errors)


def test_validate_rejects_a_longer_add_cycle_hanging_off_a_valid_chain():
    """MM1 chains legitimately off PR1; the other three close a ring. The
    valid part of the patch does not launder the ring."""
    ops = [
        _add("PR1", "MM1"),
        _add("BB1", "AA1"),
        _add("TT1", "BB1"),
        _add("AA1", "TT1"),
    ]
    errors = validate_patch(_plan(), Patch(ops=ops))
    assert any(
        "cyclic add anchors" in e and all(h in e for h in ("AA1", "BB1", "TT1"))
        for e in errors
    )
    assert not any("MM1" in e for e in errors)


# --- the patch this rule was refusing, replayed ---------------------------


def _plan_with_a_real_event():
    """The pre-patch plan behind tmbx journal entry 133: one block the
    planner did not create, a real calendar event it had to build around.
    EVT1 is the only handle in that patch the old rule accepted."""
    return Plan(
        date=date(2026, 8, 31),
        blocks=[
            Block(uid="u-evt1", h="EVT1", n="Standup", t=ET.M,
                  p=FixedStart(st=time(10, 0), dur=timedelta(minutes=30)),
                  anchor_source="calendar"),
        ],
    )


def _journal_133_ops(root_type=ET.BG):
    """Entry 133's ops verbatim, minus the `why`/`d` nulls: one fs anchor
    (the morning ritual), one add hung off the real event, and twelve
    `ap` blocks each naming the block before it. Thirteen of the fourteen
    were refused with "anchor <handle> not found".

    ``root_type`` is the one liberty taken with the record, and only the
    two resolution tests below use it. Entry 133 typed the morning ritual
    `BG`, and `Plan.resolve` keeps BG blocks out of the chain entirely —
    a second defect in the same patch, which the two tests separate rather
    than average.
    """
    def ap(after, h, n, t, minutes):
        return AddBlock(after=after, h=h, n=n, t=t,
                        p=AfterPrev(dur=timedelta(minutes=minutes)))

    return [
        AddBlock(after=None, h="MR1", n="Morning ritual", t=root_type,
                 p=FixedStart(st=time(7, 0), dur=timedelta(hours=1)),
                 anchor_source="constraint"),
        ap("MR1", "BR1", "Breakfast", ET.H, 30),
        ap("EVT1", "DW1", "Finish the C2F deck", ET.DW, 150),
        ap("DW1", "LN1", "Lunch", ET.H, 30),
        ap("LN1", "OT1", "Oats", ET.H, 15),
        ap("OT1", "GB1", "Gym buffer (pre)", ET.BU, 15),
        ap("GB1", "GY1", "Gym", ET.H, 60),
        ap("GY1", "GB2", "Gym buffer (post)", ET.BU, 15),
        ap("GB2", "FR1", "Free / relax", ET.R, 120),
        ap("FR1", "DN1", "Dinner with Marieke", ET.H, 60),
        ap("DN1", "CU1", "Clean up after dinner", ET.R, 30),
        ap("CU1", "CH1", "Evening chill / music", ET.R, 60),
        ap("CH1", "SD1", "Shutdown ritual", ET.SW, 30),
        ap("SD1", "RD1", "Sci-fi reading", ET.R, 30),
    ]


def test_the_refused_journal_133_patch_now_applies_as_one_chain():
    patch = Patch(ops=_journal_133_ops())
    assert validate_patch(_plan_with_a_real_event(), patch) == []

    result = apply_ops(_plan_with_a_real_event(), patch, mint_uid=_mint)
    assert [b.h for b in result.blocks] == [
        "MR1", "BR1", "EVT1", "DW1", "LN1", "OT1", "GB1", "GY1", "GB2",
        "FR1", "DN1", "CU1", "CH1", "SD1", "RD1",
    ]
    # The point of the chain: one pinned block in fourteen. Everything
    # else still says "when the one before me ends".
    pinned = [b.h for b in result.blocks if b.p.a in ("fs", "fw")]
    assert pinned == ["MR1", "EVT1"]


def test_the_journal_133_patch_lands_the_same_plan_however_its_ops_are_listed():
    """Fourteen ops is too many to permute exhaustively; reversing them
    puts every anchor after the op naming it, which is the ordering a
    list-order implementation cannot survive."""
    forward = _journal_133_ops()
    reverse = list(reversed(forward))
    a = apply_ops(_plan_with_a_real_event(), Patch(ops=forward), mint_uid=lambda: "u-f")
    b = apply_ops(_plan_with_a_real_event(), Patch(ops=reverse), mint_uid=lambda: "u-f")
    assert [x.h for x in a.blocks] == [x.h for x in b.blocks]


def test_the_journal_133_chain_resolves_into_a_day_once_its_root_joins_the_chain():
    """Applying the patch was never the whole story, and this is what the
    planner was actually reaching for: one pinned start at 07:00, one
    calendar event it cannot move, and twelve blocks that each begin when
    the one before them ends — a day that still shifts when any of it
    does.
    """
    patch = Patch(ops=_journal_133_ops(root_type=ET.PR))
    result = apply_ops(_plan_with_a_real_event(), patch, mint_uid=_mint)

    rows = result.resolve(check_overlap=True)
    assert [(r.h, r.start) for r in rows][:5] == [
        ("MR1", time(7, 0)),
        ("BR1", time(8, 0)),
        ("EVT1", time(10, 0)),
        ("DW1", time(10, 30)),
        ("LN1", time(13, 0)),
    ]
    assert rows[-1].h == "RD1" and rows[-1].end == time(20, 45)
    # Every block starts no earlier than the previous one ended: a chain,
    # not fourteen independent pins that happen not to collide.
    assert all(a.end_dt <= b.start_dt for a, b in itertools.pairwise(rows))


def test_the_journal_133_patch_as_recorded_applies_but_will_not_resolve():
    """The anchor rule was the first of two things standing between that
    patch and a day. The second is the model's: it typed the 07:00 morning
    ritual `BG`, and a BG block never enters the chain, so the `ap`
    breakfast naming it has nothing to start after.

    Pinned because of what changed, not because the plan is good. Before,
    the model got "anchor MR1 not found" thirteen times over — a refusal
    naming nothing it had done wrong, which it answered by pinning the
    whole day. Now the patch lands and the refusal is a typed violation
    naming exactly one block and exactly one reason, which is a thing a
    next turn can fix.
    """
    result = apply_ops(
        _plan_with_a_real_event(), Patch(ops=_journal_133_ops()), mint_uid=_mint
    )

    with pytest.raises(PlanViolation) as excinfo:
        result.resolve(check_overlap=False)
    violation = excinfo.value.violation
    assert violation.kind is ViolationKind.UNANCHORED_AFTER_PREV
    assert [b.h for b in violation.blocks] == ["BR1"]


# ---------------------------------------------------------------------------
# SPIKE: op order is significant for adds.
#
# `after` absent means "follow the add listed before me"; `after` present is an
# override and behaves exactly as it always did. Remove, update and move are
# untouched and remain a set.
# ---------------------------------------------------------------------------


def _empty_plan():
    return Plan(date=date(2026, 8, 17), blocks=[])


def _hugo_case(**kw):
    """The measured defect, verbatim: Lunch, Gym, Walk, in that order.

    Handles chosen the way the live planner chose them — from the block
    names — which is exactly why alphabetical tie-breaking reversed the
    day: BW1 < MG1 < ZL1 is the opposite of the order they were written.

    Lunch is pinned only because a chain needs one fs block somewhere or
    the patch is refused before any of this is reached. It is the anchor,
    not the subject.
    """
    def hour(h, n, minutes):
        return AddBlock(h=h, n=n, t=ET.H, p=AfterPrev(dur=timedelta(minutes=minutes)), **kw)

    lunch = AddBlock(h="ZL1", n="Lunch", t=ET.H, anchor_source="user",
                     p=FixedStart(st=time(12, 0), dur=timedelta(minutes=60)), **kw)
    return [lunch, hour("MG1", "Gym", 60), hour("BW1", "Walk", 20)]


def test_three_adds_all_saying_END_now_append_in_the_order_listed():
    """The measurement that opened this spike. `after: "END"` read as
    "append in order" and was not: all three resolved to the same trailing
    position and were then sorted by handle, so the day came out
    Walk, Gym, Lunch. Nothing in the patch was wrong.
    """
    result = apply_ops(
        _empty_plan(), Patch(ops=_hugo_case(after="END")), mint_uid=_mint
    )
    assert [b.h for b in result.blocks] == ["ZL1", "MG1", "BW1"]


def test_the_same_three_adds_need_no_anchors_at_all():
    """The point of the change: the sequence is in the ops list, so the
    planner does not restate it. Same day as the explicit-END patch above,
    with three fields fewer.
    """
    result = apply_ops(_empty_plan(), Patch(ops=_hugo_case()), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["ZL1", "MG1", "BW1"]


def test_an_omitted_after_chains_onto_the_previous_add():
    """Not merely "lands after it in the list" — the anchor really is the
    previous add's handle, so an add listed between two others is inserted
    between them rather than appended past them.
    """
    ops = [
        _add("PR1", "ZZ1"),
        AddBlock(h="YY1", n="Y", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
    ]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "ZZ1", "YY1", "DW1", "DW2"]


def test_the_first_add_with_no_after_prepends():
    """There is no previous add to follow, so it means what `after: null`
    means. Stated as its own test because it is the one place the rule has
    to answer a question the list cannot: an empty history.
    """
    ops = [AddBlock(h="AA1", n="A", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5)))]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["AA1", "PR1", "DW1", "DW2"]


def test_a_whole_anchorless_chain_lands_at_the_front_of_an_existing_plan():
    """The consequence of the rule above, and the sharpest edge the spike
    exposed: the first add prepends, so a chain written with no anchors at
    all goes BEFORE everything already on the plan, not after it. Pinned
    deliberately. It is right for building an empty day and surprising for
    adding to a full one, and the fix — if it is one — is a different
    default for the first add, not a different rule for the rest.
    """
    result = apply_ops(_plan(), Patch(ops=_hugo_case()), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["ZL1", "MG1", "BW1", "PR1", "DW1", "DW2"]


def test_absent_and_null_are_different_patches():
    """The distinction the whole mechanism rests on. Both ops parse; one
    says "follow the block before me" and the other says "go to the
    front", and a schema that could not tell them apart would have to pick
    one and silently lose the other.
    """
    body = {"op": "add", "h": "BB1", "n": "B", "t": "BU", "p": {"a": "ap", "dur": "PT5M"}}
    first = {"op": "add", "h": "AA1", "n": "A", "t": "BU", "p": {"a": "ap", "dur": "PT5M"},
             "after": "PR1"}

    absent = Patch.model_validate({"ops": [first, body]})
    explicit_null = Patch.model_validate({"ops": [first, {**body, "after": None}]})

    assert [b.h for b in apply_ops(_plan(), absent, mint_uid=_mint).blocks] == [
        "PR1", "AA1", "BB1", "DW1", "DW2",
    ]
    assert [b.h for b in apply_ops(_plan(), explicit_null, mint_uid=_mint).blocks] == [
        "BB1", "PR1", "AA1", "DW1", "DW2",
    ]


def test_an_explicit_after_overrides_the_previous_add_without_breaking_the_chain():
    """The override is local. CC1 jumps to PR1; DD1, which says nothing,
    follows CC1 — because "the previous add" is a fact about the list, and
    an op that named its own anchor is still in the list.
    """
    ops = [
        _add("DW1", "AA1"),
        AddBlock(h="BB1", n="B", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
        _add("PR1", "CC1"),
        AddBlock(h="DD1", n="D", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5))),
    ]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == [
        "PR1", "CC1", "DD1", "DW1", "AA1", "BB1", "DW2",
    ]


def test_removes_updates_and_moves_are_still_a_set():
    """The spike changed one phase. Every op that is not an add still
    resolves against handles and anchors alone, so permuting them is
    still a no-op on the result.
    """
    ops = [
        RemoveBlock(h="DW2"),
        UpdateBlock(h="DW1", n="Renamed"),
        MoveBlock(h="PR1", after="END"),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple((b.h, b.n) for b in plan.blocks))
    assert len(results) == 1


def test_an_anchorless_chain_can_never_be_cyclic():
    """Every PREV edge points at an add listed earlier, so the graph is
    acyclic by construction — there is no way to write a ring out of
    omitted anchors. Worth a test rather than a comment: it is the
    property that lets the layering stay a backstop instead of becoming
    the thing a planner has to reason about.
    """
    ops = [
        AddBlock(h=h, n=h, t=ET.BU, p=AfterPrev(dur=timedelta(minutes=5)))
        for h in ("AA1", "BB1", "CC1", "DD1", "EE1")
    ]
    assert validate_patch(_plan(), Patch(ops=ops)) == []
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks][:5] == ["AA1", "BB1", "CC1", "DD1", "EE1"]


def test_an_explicit_forward_reference_still_needs_the_layering():
    """Order narrows the dependency graph; it does not replace it. An
    explicit anchor may still name an add listed further down, and that
    add has to be placed first — which is what `_apply_adds`' layers are
    for, and why the spike did not delete them.
    """
    ops = [_add("ZZ1", "AA1"), _add("PR1", "ZZ1")]
    result = apply_ops(_plan(), Patch(ops=ops), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "ZZ1", "AA1", "DW1", "DW2"]


def test_prev_on_a_move_is_refused_by_name():
    """A move has no previous — it names a position on the plan as
    rendered, and the spike deliberately left that alone. A model that
    carried the add rule across gets told which rule it crossed, not that
    PREV is a handle it forgot to create.
    """
    errors = validate_patch(_plan(), Patch(ops=[MoveBlock(h="DW2", after="PREV")]))
    assert any("add-only anchor" in e and "DW2" in e for e in errors)
    assert not any("not found" in e for e in errors)


def test_two_moves_sharing_an_anchor_still_tie_break_by_handle():
    """The asymmetry `_insert_batch` documents, pinned so it cannot drift
    into being fixed by accident: adds rank by list position, moves rank
    by handle. A move's `after` is always explicit, so two moves sharing
    one anchor have stated nothing about their order, and the spike had no
    signal to promote.
    """
    ops = [MoveBlock(h="DW2", after="PR1"), MoveBlock(h="DW1", after="PR1")]
    forward = apply_ops(_plan(), Patch(ops=ops), mint_uid=lambda: "u")
    reverse = apply_ops(_plan(), Patch(ops=list(reversed(ops))), mint_uid=lambda: "u")
    assert [b.h for b in forward.blocks] == ["PR1", "DW1", "DW2"]
    assert [b.h for b in reverse.blocks] == [b.h for b in forward.blocks]


def test_the_journal_133_day_needs_no_anchors_except_the_one_that_is_real():
    """Entry 133 rewritten under the new rule. Fourteen blocks, and the
    only `after` left is the one that says something the list could not:
    the deep-work block belongs after a calendar event tmbx does not own.
    Thirteen `after` fields become zero, and the day is identical.
    """
    def ap(h, n, t, minutes, **kw):
        return AddBlock(h=h, n=n, t=t, p=AfterPrev(dur=timedelta(minutes=minutes)), **kw)

    ops = [
        AddBlock(after=None, h="MR1", n="Morning ritual", t=ET.PR,
                 p=FixedStart(st=time(7, 0), dur=timedelta(hours=1)),
                 anchor_source="constraint"),
        ap("BR1", "Breakfast", ET.H, 30),
        ap("DW1", "Finish the C2F deck", ET.DW, 150, after="EVT1"),
        ap("LN1", "Lunch", ET.H, 30),
        ap("OT1", "Oats", ET.H, 15),
        ap("GB1", "Gym buffer (pre)", ET.BU, 15),
        ap("GY1", "Gym", ET.H, 60),
        ap("GB2", "Gym buffer (post)", ET.BU, 15),
        ap("FR1", "Free / relax", ET.R, 120),
        ap("DN1", "Dinner with Marieke", ET.H, 60),
        ap("CU1", "Clean up after dinner", ET.R, 30),
        ap("CH1", "Evening chill / music", ET.R, 60),
        ap("SD1", "Shutdown ritual", ET.SW, 30),
        ap("RD1", "Sci-fi reading", ET.R, 30),
    ]
    explicit = Patch(ops=_journal_133_ops(root_type=ET.PR))
    implicit = Patch(ops=ops)

    assert validate_patch(_plan_with_a_real_event(), implicit) == []
    a = apply_ops(_plan_with_a_real_event(), explicit, mint_uid=lambda: "u")
    b = apply_ops(_plan_with_a_real_event(), implicit, mint_uid=lambda: "u")
    assert [x.h for x in b.blocks] == [x.h for x in a.blocks]

    rows = b.resolve(check_overlap=True)
    assert [(r.h, r.start) for r in rows][:5] == [
        ("MR1", time(7, 0)),
        ("BR1", time(8, 0)),
        ("EVT1", time(10, 0)),
        ("DW1", time(10, 30)),
        ("LN1", time(13, 0)),
    ]
    pinned = [x.h for x in b.blocks if x.p.a in ("fs", "fw")]
    assert pinned == ["MR1", "EVT1"]
