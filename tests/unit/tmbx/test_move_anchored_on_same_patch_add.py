"""A move cannot anchor on a handle the same patch adds, and the refusal says so (#304).

Ops apply remove, update, move, add -- so at move time the added block does
not exist yet. That is a rule, and it was being reported as a typo:
``anchor BF1 not found``. On 2026-09-04 a model read it as one and resent
the identical patch three times until the attempt guard stopped it. The
``PREV``-on-a-move refusal already names the rule it crossed; this is the
same courtesy for a same-patch handle, with the move that would have worked.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from tmbx.core.models import ET, AfterPrev, Block, FixedStart, Plan
from tmbx.core.ops import AddBlock, MoveBlock, Patch, apply_ops, validate_patch


def _plan() -> Plan:
    return Plan(
        date=date(2026, 9, 4),
        blocks=[
            Block(uid="u1", h="DW1", n="Deep work", t=ET.DW,
                  p=FixedStart(st=time(12, 30), dur=timedelta(minutes=90)), anchor_source="user"),
            Block(uid="u2", h="LN1", n="Lunch", t=ET.H, p=AfterPrev(dur=timedelta(minutes=30))),
        ],
    )


def _buffer_after(anchor: str) -> AddBlock:
    return AddBlock(after=anchor, h="BF1", n="Buffer", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=15)))


def test_the_refusal_names_the_rule_and_the_move_that_would_work():
    """The journal's patch, verbatim in shape: add BF1 after DW1, move LN1 after BF1."""
    errors = validate_patch(_plan(), Patch(ops=[_buffer_after("DW1"), MoveBlock(h="LN1", after="BF1")]))
    [error] = errors
    assert "not found" not in error, error
    assert "BF1" in error and "LN1" in error
    assert "added by this patch" in error
    assert "before adds" in error or "applied before" in error


def test_a_genuinely_missing_anchor_is_still_reported_as_not_found():
    [error] = validate_patch(_plan(), Patch(ops=[MoveBlock(h="LN1", after="ZZ9")]))
    assert "anchor ZZ9 not found" in error


def test_apply_refuses_it_the_same_way():
    with pytest.raises(ValueError, match="added by this patch"):
        apply_ops(_plan(), Patch(ops=[_buffer_after("DW1"), MoveBlock(h="LN1", after="BF1")]),
                  mint_uid=lambda: "u-new")


def test_the_add_alone_already_puts_the_buffer_before_lunch():
    """What the refusal tells the model to do instead, and why it is equivalent."""
    result = apply_ops(_plan(), Patch(ops=[_buffer_after("DW1")]), mint_uid=lambda: "u-new")
    assert [b.h for b in result.blocks] == ["DW1", "BF1", "LN1"]
