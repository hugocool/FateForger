"""The Stage 1 gate is arithmetic over the snapshot: feed a matrix, read a Gate.

No model is anywhere near this module. A matrix is what a judge wrote into the
snapshot; this decides what it means for the stage, and the same function
answers the kernel and the interpreter so the card and the decision set cannot
disagree about whether Next exists.
"""
from __future__ import annotations

from datetime import date

from fateforger.agents.timeboxing.elicitation import (
    ALL_CELLS,
    CRITERIA,
    ROWS,
    CoverageMatrix,
    RowStats,
    coverage_matrix,
    ranked_open_cells,
    stage1_gate,
)
from fateforger.agents.timeboxing.session_contracts import (
    CellRef,
    DayType,
    FactKind,
    PlannerAssumption,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    coverage_fact_id,
)

DAY = date(2026, 9, 8)  # a Tuesday


def _snapshot(matrix: CoverageMatrix | None) -> PlanningSessionSnapshot:
    facts = []
    if matrix is not None:
        facts.append(
            PlanningFact(
                fact_id=coverage_fact_id(DAY),
                kind=FactKind.COVERAGE_MATRIX,
                value=matrix.model_dump(mode="json"),
                source="system",
            )
        )
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=2,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=DAY, timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=facts,
    )


def _matrix(**states: str) -> CoverageMatrix:
    cells = {cell.id: "not_applicable" for cell in ALL_CELLS}
    cells.update(states)
    return CoverageMatrix(cells=cells)


def test_the_floor_has_eight_rows_and_forty_cells() -> None:
    assert len(ROWS) == 8
    assert len(CRITERIA) == 5
    assert len(ALL_CELLS) == 40
    assert "unplaced" in ROWS and "request" in ROWS


def test_no_matrix_means_nothing_is_open() -> None:
    gate = stage1_gate(_snapshot(None))
    assert gate.open_cells == []
    assert gate.day_label == "working Tuesday"


def test_an_uncovered_cell_holds_the_gate_open() -> None:
    gate = stage1_gate(_snapshot(_matrix(**{"elicit.body.unclear": "uncovered"})))
    assert gate.open_cells == [CellRef(row="body", criterion="unclear")]


def test_an_assumption_closes_its_cell() -> None:
    """Forcing past a cell is forcing past it, not for one turn only."""

    cell_id = "elicit.body.unclear"
    snapshot = _snapshot(_matrix(**{cell_id: "uncovered"})).model_copy(
        update={
            "assumptions": [
                PlannerAssumption(
                    assumption_id="a1",
                    requirement_id=cell_id,
                    value="assume a normal day",
                    why_needed="user forced past",
                    filed_by="user",
                )
            ]
        }
    )
    gate = stage1_gate(snapshot)
    assert gate.open_cells == []


def test_covered_and_not_applicable_do_not_hold_it() -> None:
    gate = stage1_gate(
        _snapshot(_matrix(**{"elicit.body.unclear": "covered", "elicit.fixed.unclear": "not_applicable"}))
    )
    assert gate.open_cells == []


def test_ranking_prefers_rows_with_rules_then_musts_then_criterion_order() -> None:
    matrix = _matrix(
        **{
            "elicit.fragile.tacit_knowledge": "uncovered",
            "elicit.body.unclear": "uncovered",
            "elicit.body.tacit_assumptions": "uncovered",
            "elicit.movement.unclear": "uncovered",
        }
    )
    matrix = matrix.model_copy(
        update={
            "rows": {
                "body": RowStats(rule_count=5, must_count=1, stated=0),
                "fragile": RowStats(rule_count=7, must_count=0, stated=1),
                "movement": RowStats(rule_count=0, must_count=0, stated=0),
            }
        }
    )
    ranked = ranked_open_cells(matrix)
    assert [c.id for c in ranked] == [
        "elicit.body.tacit_assumptions",
        "elicit.body.unclear",
        "elicit.fragile.tacit_knowledge",
        "elicit.movement.unclear",
    ]


def test_the_matrix_fact_is_read_back_by_its_stable_id() -> None:
    snapshot = _snapshot(_matrix(**{"elicit.request.unclear": "uncovered"}))
    matrix = coverage_matrix(snapshot)
    assert matrix is not None
    assert matrix.cells["elicit.request.unclear"] == "uncovered"


def test_a_malformed_matrix_fact_is_refused_not_ignored() -> None:
    import pytest

    snapshot = _snapshot(None).model_copy(
        update={
            "facts": [
                PlanningFact(
                    fact_id=coverage_fact_id(DAY),
                    kind=FactKind.COVERAGE_MATRIX,
                    value={"cells": "not a mapping"},
                    source="system",
                )
            ]
        }
    )
    with pytest.raises(ValueError):
        coverage_matrix(snapshot)


def test_an_unaskable_cell_ranks_last_among_the_cells_already_open() -> None:
    """`unaskable` orders the open list; it does not decide what is on it."""

    matrix = _matrix(
        **{
            "elicit.body.unclear": "uncovered",
            "elicit.movement.unclear": "uncovered",
        }
    )
    matrix = matrix.model_copy(
        update={
            "unaskable": ["elicit.movement.unclear"],
            "rows": {
                "body": RowStats(rule_count=5, must_count=0, stated=0),
                "movement": RowStats(rule_count=5, must_count=0, stated=0),
            }
        }
    )
    ranked = ranked_open_cells(matrix)
    assert [c.id for c in ranked] == [
        "elicit.body.unclear",
        "elicit.movement.unclear",
    ]


def test_an_unaskable_cell_that_is_covered_is_not_open() -> None:
    """The spec's gate is "met when no cell is uncovered", and nothing else.

    Unioning `unaskable` into the open set instead made a cell a judge had
    already marked covered hold the gate shut, so Next could never appear on a
    day whose matrix listed anything unaskable.
    """

    matrix = _matrix(
        **{"elicit.body.unclear": "uncovered", "elicit.movement.unclear": "covered"}
    )
    # movement.unclear is covered, fixed.unclear is not_applicable; being
    # listed unaskable changes neither.
    matrix = matrix.model_copy(
        update={"unaskable": ["elicit.movement.unclear", "elicit.fixed.unclear"]}
    )
    assert [c.id for c in ranked_open_cells(matrix)] == ["elicit.body.unclear"]


def test_the_gate_is_met_when_every_unaskable_cell_is_covered() -> None:
    matrix = _matrix()
    matrix = matrix.model_copy(update={"unaskable": ["elicit.body.unclear"]})
    assert ranked_open_cells(matrix) == []


def test_missing_cell_ids_are_refused() -> None:
    import pytest

    cells = {cell.id: "not_applicable" for cell in ALL_CELLS}
    # Remove one cell
    del cells[ALL_CELLS[0].id]
    with pytest.raises(ValueError, match="missing"):
        CoverageMatrix(cells=cells)


def test_extra_cell_ids_are_refused() -> None:
    import pytest

    cells = {cell.id: "not_applicable" for cell in ALL_CELLS}
    cells["elicit.bogus.cell"] = "uncovered"
    with pytest.raises(ValueError, match="extra"):
        CoverageMatrix(cells=cells)


def test_coverage_matrix_returns_none_for_snapshot_with_no_planning_day() -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=2,
        owner_user_id="U1",
        planning_day=None,
        facts=[],
    )
    assert coverage_matrix(snapshot) is None


def test_stage1_gate_raises_for_snapshot_with_no_planning_day() -> None:
    import pytest

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=2,
        owner_user_id="U1",
        planning_day=None,
        facts=[],
    )
    with pytest.raises(ValueError, match="locked planning day"):
        stage1_gate(snapshot)
