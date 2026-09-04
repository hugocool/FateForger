"""The spec Stage 1 reasons against, and the arithmetic gate over it.

Two layers meet here. The concern-floor below is the only authored list in the
Stage 1 design: six concerns at the level of what a day has to have settled,
plus two rows that are not concerns but places a gap can live. Anchors, the
second layer, are minted by the memory server from the user's own words and
never appear here; a judge places them under rows and records the placement in
the matrix fact.

Nothing in this module calls a model. `stage1_gate` reads the matrix a judge
wrote into the snapshot and says what is still open; the kernel and the
interpreter both ask it, so the outcome and the decision set agree about
whether Next exists.

Design: docs/superpowers/specs/2026-09-04-stage1-elicitation-design.md
Measurements behind the criterion wording and the row choice:
docs/superpowers/research/2026-09-04-stage1-spike-findings.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .session_contracts import (
    CellRef,
    FactKind,
    Gate,
    PlanningDay,
    PlanningSessionSnapshot,
    coverage_fact_id,
)


@dataclass(frozen=True, slots=True)
class Concern:
    key: str
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class Criterion:
    key: str
    label: str
    question: str


#: Layer 1. Hugo corrects this before any spike runs (#283); the six
#: planning-meta rules that fit no concern are the known open input.
CONCERNS: tuple[Concern, ...] = (
    Concern("bounded", "how the day is bounded", "when it starts and ends, what frames it"),
    Concern("fixed", "what is fixed", "events, appointments, arrivals that do not move"),
    Concern("movement", "movement and transitions", "commutes, travel, the gaps between fixed things"),
    Concern("body", "body", "food, sleep, energy, exercise; the physical constraints on attention"),
    Concern("fragile", "fragile intentions", "the things that only happen if protected"),
    Concern("not_today", "what today is not", "rules that usually hold and do not today"),
)

#: Not concerns: places a gap can live that no concern covers. `unplaced` holds
#: anchors the placement call could not put under a concern; `request` holds
#: what the user said they want from the day, which the fixture showed carrying
#: the gap every other row was reporting.
EXTRA_ROWS: tuple[Concern, ...] = (
    Concern("unplaced", "rules under no concern", "anchors the placement could not put anywhere"),
    Concern("request", "what you asked for today", "the stated request for this day"),
)

ROWS: dict[str, Concern] = {c.key: c for c in (*CONCERNS, *EXTRA_ROWS)}

#: The five follow-up criteria of Singhal et al., with one discriminator added
#: to `alternatives`: as the paper words it the criterion was uncovered on
#: every row of both spike runs, and a criterion that can never be covered
#: before planning is a gate that never opens.
CRITERIA: tuple[Criterion, ...] = (
    Criterion("tacit_assumptions", "assumptions", "Are the assumptions behind what is on record justified for this day, or unstated?"),
    Criterion("alternatives", "alternatives", "Where a rule here is at risk given what the user said today, has an alternative been considered?"),
    Criterion("unclear", "clarity", "Is anything here ambiguous or underspecified for placing it on today's timeline?"),
    Criterion("contradictory", "contradictions", "Do any statements or rules here contradict each other, or the user's request?"),
    Criterion("tacit_knowledge", "unstated knowledge", "Is there knowledge only the user has, such as durations or arrivals, that is unstated and needed?"),
)

CRITERION_BY_KEY: dict[str, Criterion] = {c.key: c for c in CRITERIA}

ALL_CELLS: tuple[CellRef, ...] = tuple(
    CellRef(row=row, criterion=criterion.key) for row in ROWS for criterion in CRITERIA
)

_WEEKDAYS: tuple[str, ...] = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

CellState = Literal["covered", "uncovered", "not_applicable"]


class RowStats(BaseModel):
    """Counts over minted fields a judge records per row when it classifies.

    Ranking reads these and nothing else: no row is marked important by hand.
    """

    model_config = ConfigDict(extra="forbid")

    rule_count: int = Field(ge=0, default=0)
    must_count: int = Field(ge=0, default=0)
    stated: int = Field(ge=0, default=0)


class CoverageMatrix(BaseModel):
    """The Stage 1 coverage state, as stored in the `coverage:{day}` fact."""

    model_config = ConfigDict(extra="forbid")

    cells: dict[str, CellState]
    #: anchor uid -> row key, the placement these cells were classified against
    placement: dict[str, str] = Field(default_factory=dict)
    rows: dict[str, RowStats] = Field(default_factory=dict)
    #: still open, ranked after every askable cell, so the gate line shows it and
    #: the kernel asks it only when nothing askable remains
    unaskable: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cells_are_complete(self) -> CoverageMatrix:
        """Ensure cells dict contains exactly the forty ALL_CELLS, no more, no fewer."""
        expected = {cell.id for cell in ALL_CELLS}
        actual = set(self.cells.keys())
        if actual != expected:
            missing = expected - actual
            extra = actual - expected
            msg = "coverage matrix cells are incomplete"
            if missing:
                msg += f"; missing: {missing}"
            if extra:
                msg += f"; extra: {extra}"
            raise ValueError(msg)

        # Validate that all unaskable ids are valid cell ids
        unaskable_set = set(self.unaskable)
        invalid_unaskable = unaskable_set - expected
        if invalid_unaskable:
            raise ValueError(f"unaskable contains invalid cell ids: {invalid_unaskable}")

        return self


def coverage_matrix(snapshot: PlanningSessionSnapshot) -> CoverageMatrix | None:
    """The matrix for the locked day, or None when no judge has written one.

    None is "no elicitation has run", which is the honest state of a session
    before the spikes land; it is not "gate unmet". A fact that exists but
    does not parse is refused: a malformed matrix must not read as an empty one.
    """
    if snapshot.planning_day is None:
        return None
    wanted = coverage_fact_id(snapshot.planning_day.date)
    for fact in snapshot.facts:
        if fact.kind is FactKind.COVERAGE_MATRIX and fact.fact_id == wanted:
            if not isinstance(fact.value, dict):
                raise ValueError(f"coverage matrix fact {wanted} is not an object")
            return CoverageMatrix.model_validate(fact.value)
    return None


def ranked_open_cells(
    matrix: CoverageMatrix, assumed: frozenset[str] = frozenset()
) -> list[CellRef]:
    """Uncovered cells by expected value, every term a count over minted fields.

    A row with rules or stated facts before one with neither; a row carrying a
    `must` before one carrying only `should`s; then the criterion order above.

    A cell is open iff its state is `uncovered` -- the spec's gate is "met when
    no cell is uncovered", so `unaskable` only ranks a cell last among the ones
    already open. Unioning the two lists instead held the gate shut on cells a
    judge had marked covered or not applicable.

    `assumed` is a set of cell ids a `PlannerAssumption` already answers --
    the user forced past them, and the matrix (unaware of assumptions) would
    otherwise keep reporting them uncovered forever.
    """
    order = {c.key: i for i, c in enumerate(CRITERIA)}
    unaskable_set = set(matrix.unaskable)
    open_cells = [
        cell for cell in ALL_CELLS
        if matrix.cells[cell.id] == "uncovered" and cell.id not in assumed
    ]

    def key(cell: CellRef) -> tuple[int, int, int, int]:
        is_unaskable = 1 if cell.id in unaskable_set else 0
        stats = matrix.rows.get(cell.row, RowStats())
        has_content = 1 if (stats.rule_count + stats.stated) > 0 else 0
        has_must = 1 if stats.must_count > 0 else 0
        return (is_unaskable, -has_content, -has_must, order[cell.criterion])

    return sorted(open_cells, key=key)


def day_label(planning_day: PlanningDay) -> str:
    """The day type and the weekday, both minted by the host; the weekday name comes from a fixed table so the label does not follow the process locale."""
    weekday_name = _WEEKDAYS[planning_day.date.isoweekday() - 1]
    return f"{planning_day.day_type.value} {weekday_name}"


def stage1_gate(snapshot: PlanningSessionSnapshot) -> Gate:
    """What Stage 1 still needs. Arithmetic over the snapshot; called by the
    kernel for its outcome and by the interpreter for its decision set.

    A cell a `PlannerAssumption` already answers is subtracted here, not left
    to each caller: the matrix itself never changes when the user forces past
    a cell, so every reader of this function must see the same "closed"
    verdict or the gate would depend on which call site asked.
    """
    if snapshot.planning_day is None:
        raise ValueError("stage1_gate needs a locked planning day")
    matrix = coverage_matrix(snapshot)
    assumed = frozenset(a.requirement_id for a in snapshot.assumptions)
    open_cells = [] if matrix is None else ranked_open_cells(matrix, assumed)
    return Gate(open_cells=open_cells, day_label=day_label(snapshot.planning_day))


def row_label(key: str) -> str:
    return ROWS[key].label


def criterion_label(key: str) -> str:
    return CRITERION_BY_KEY[key].label
