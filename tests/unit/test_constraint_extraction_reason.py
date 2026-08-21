# tests/unit/test_constraint_extraction_reason.py
"""Extraction reason must be persisted onto each constraint's hints."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fateforger.agents.timeboxing.agent import _stamp_extraction_reason
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
)


def _constraint(**hints: Any) -> Constraint:
    return Constraint(
        name="Dinner",
        description="Dinner at 18:30",
        necessity=ConstraintNecessity.MUST,
        user_id="u1",
        hints=dict(hints),
    )


def test_stamps_reason_onto_empty_hints() -> None:
    c = _constraint()
    _stamp_extraction_reason([c], reason="graphflow_turn")
    assert c.hints["extraction_reason"] == "graphflow_turn"


def test_preserves_existing_hints() -> None:
    c = _constraint(uid="abc123")
    _stamp_extraction_reason([c], reason="refine_background_memory")
    assert c.hints["uid"] == "abc123"
    assert c.hints["extraction_reason"] == "refine_background_memory"


def test_does_not_overwrite_existing_reason() -> None:
    """First extraction wins — a later pass must not relabel provenance."""
    c = _constraint()
    _stamp_extraction_reason([c], reason="graphflow_turn")
    _stamp_extraction_reason([c], reason="refine_background_memory")
    assert c.hints["extraction_reason"] == "graphflow_turn"


def test_does_not_overwrite_falsy_existing_reason() -> None:
    """Presence, not truthiness, guards the write.

    An empty-string placeholder (stale data, or any future writer that stores
    one) must still block a later pass from relabelling provenance.
    """
    c = _constraint(extraction_reason="")
    _stamp_extraction_reason([c], reason="graphflow_turn")
    assert c.hints["extraction_reason"] == ""


def test_tolerates_empty_list() -> None:
    _stamp_extraction_reason([], reason="graphflow_turn")


def test_skips_object_without_hints_attribute() -> None:
    obj = SimpleNamespace()  # no `hints` attribute at all
    _stamp_extraction_reason([obj], reason="graphflow_turn")
    assert not hasattr(obj, "hints")


def test_skips_non_dict_hints() -> None:
    obj = SimpleNamespace(hints="not-a-dict")
    _stamp_extraction_reason([obj], reason="graphflow_turn")
    assert obj.hints == "not-a-dict"


def test_tolerates_none_entry_in_iterable() -> None:
    c = _constraint()
    _stamp_extraction_reason([None, c], reason="graphflow_turn")
    assert c.hints["extraction_reason"] == "graphflow_turn"
