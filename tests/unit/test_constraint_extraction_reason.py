# tests/unit/test_constraint_extraction_reason.py
"""Extraction reason must be persisted onto each constraint's hints."""
from __future__ import annotations

from fateforger.agents.timeboxing.agent import _stamp_extraction_reason
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
)


def _constraint(**hints):
    return Constraint(
        name="Dinner",
        description="Dinner at 18:30",
        necessity=ConstraintNecessity.MUST,
        user_id="u1",
        hints=dict(hints),
    )


def test_stamps_reason_onto_empty_hints():
    c = _constraint()
    _stamp_extraction_reason([c], reason="graphflow_turn")
    assert c.hints["extraction_reason"] == "graphflow_turn"


def test_preserves_existing_hints():
    c = _constraint(uid="abc123")
    _stamp_extraction_reason([c], reason="refine_background_memory")
    assert c.hints["uid"] == "abc123"
    assert c.hints["extraction_reason"] == "refine_background_memory"


def test_does_not_overwrite_existing_reason():
    """First extraction wins — a later pass must not relabel provenance."""
    c = _constraint(extraction_reason="graphflow_turn")
    _stamp_extraction_reason([c], reason="refine_background_memory")
    assert c.hints["extraction_reason"] == "graphflow_turn"


def test_tolerates_empty_list():
    _stamp_extraction_reason([], reason="graphflow_turn")
