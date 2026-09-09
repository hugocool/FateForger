"""The seam between the coverage spec and the probe voice (#286).

Three swaps, each leaving the other half untouched. If which cells are open is
the same with the words stubbed, and the words still render with the spec
stubbed, the loop composes: the concern-floor and the phrasing can change on
different schedules, which is what makes a growing anchor layer safe.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from fateforger.agents.timeboxing.elicitation import CoverageMatrix, stage1_gate
from fateforger.agents.timeboxing.elicitation_judges import Judges, Placement, elicit
from fateforger.agents.timeboxing.session_contracts import (
    PlanningFact,
    PlanningSessionSnapshot,
    ProbeDraft,
)
from tests.fixtures.stage1.days import FIXTURE_DAYS, snapshot_for

GYM = {"uid": "a-gym", "name": "gym"}
ROWS = [
    {"uid": "c-oats", "name": "Oats before gym", "description": "Eat oats two hours before the gym.", "necessity": "must", "anchors": [GYM]},
    {"uid": "c-exit", "name": "Block exit criteria", "description": "Every block ends with an exit criterion.", "necessity": "must", "anchors": []},
]


class _Placement:
    async def place(self, *, anchors, unanchored_rules, session_key):  # noqa: ANN001
        return Placement(anchors={"a-gym": "body"}, rules={"c-exit": "method"})


NOW = datetime(2026, 9, 8, 10, 9, tzinfo=ZoneInfo("Europe/Amsterdam"))


class _Coverage:
    def __init__(self, table: dict[str, str]) -> None:
        self.table = table

    async def classify(self, *, cell, rules, stated, request, session_key, now, planning_day):  # noqa: ANN001
        return self.table.get(cell.id, "covered"), "stub"


class _FixedWords:
    """The words half, stubbed to one string for every cell."""

    async def generate(self, *, cell, rules_full, conversation, request, session_key, now, planning_day):  # noqa: ANN001
        return ProbeDraft(cell_id=cell.id, question="Tell me more?", why_needed="fixed")


class _NoWords:
    async def generate(self, *, cell, rules_full, conversation, request, session_key, now, planning_day):  # noqa: ANN001
        return None


def _with_matrix(snapshot: PlanningSessionSnapshot, fact: PlanningFact) -> PlanningSessionSnapshot:
    return snapshot.model_copy(update={"facts": [*snapshot.facts, fact]})


def _run(snapshot, judges):
    return asyncio.run(elicit(snapshot, ROWS, judges, session_key=snapshot.session_key, now=NOW))


def test_swap_one_the_open_set_is_the_same_with_the_words_stubbed() -> None:
    # Four uncovered cells, one more than `elicit`'s `generate_for` default of
    # three, so the two runs differ in how many cells went unaskable.
    table = {
        "elicit.body.unclear": "uncovered",
        "elicit.body.tacit_assumptions": "uncovered",
        "elicit.method.contradictory": "uncovered",
        "elicit.method.tacit_knowledge": "uncovered",
    }
    for day in FIXTURE_DAYS:
        snapshot = snapshot_for(day, ROWS)
        with_words = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage(table), probe=_FixedWords()))
        without = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage(table), probe=_NoWords()))
        gate_a = stage1_gate(_with_matrix(snapshot, with_words.matrix_fact))
        gate_b = stage1_gate(_with_matrix(snapshot, without.matrix_fact))
        # The open *set* is independent of the words. The *order* is not, by
        # design: a cell whose probe could not be grounded is recorded in
        # `unaskable` and `ranked_open_cells` sorts it last, so the gate line
        # shows askable cells first. The two-cell version of this test hid that
        # coupling because both open cells went unaskable.
        assert {c.id for c in gate_a.open_cells} == {c.id for c in gate_b.open_cells}
        assert gate_a.open_cells, day.key
        matrix_b = CoverageMatrix.model_validate(without.matrix_fact.value)
        assert len(matrix_b.unaskable) == 3  # the three the generator was asked and refused
        assert {c.id for c in gate_b.open_cells[-3:]} == set(matrix_b.unaskable)
        closed = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage({}), probe=_NoWords()))
        assert stage1_gate(_with_matrix(snapshot, closed.matrix_fact)).open_cells == []


def test_swap_two_the_words_render_with_the_spec_stubbed_to_one_cell() -> None:
    """Fixed uncovered cell, real ranking, real probe carrying: the draft
    reaches the result and names the cell the spec opened."""
    snapshot = snapshot_for(FIXTURE_DAYS[0], ROWS)
    result = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage({"elicit.body.tacit_knowledge": "uncovered"}), probe=_FixedWords()))
    assert [p.cell_id for p in result.probes] == ["elicit.body.tacit_knowledge"]
    assert result.probes[0].question == "Tell me more?"


def test_swap_three_everything_unplaced_still_opens_the_gate_and_drops_nothing() -> None:
    class _Unplaced:
        async def place(self, *, anchors, unanchored_rules, session_key):  # noqa: ANN001
            return Placement(anchors={a["uid"]: "unplaced" for a in anchors}, rules={r["uid"]: "unplaced" for r in unanchored_rules})

    snapshot = snapshot_for(FIXTURE_DAYS[0], ROWS)
    result = _run(snapshot, Judges(placement=_Unplaced(), coverage=_Coverage({}), probe=_NoWords()))
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert matrix.rows["unplaced"].rule_count == 2
    assert stage1_gate(_with_matrix(snapshot, result.matrix_fact)).open_cells == []
