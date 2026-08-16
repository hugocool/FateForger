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
