"""The three Stage 1 judges and the orchestrator, with the model stubbed.

Every assertion is on what was sent and what the answer became -- never on
the model's words. The judges follow `DayFrameJudge`; the stub client is the
one `tests/unit/test_day_frame_on_record.py` uses.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from fateforger.agents.timeboxing.elicitation import (
    ALL_CELLS,
    CONCERNS,
    ROWS,
    CoverageMatrix,
    ranked_open_cells,
)
from fateforger.agents.timeboxing.elicitation_judges import (
    PLACEMENT_TARGETS,
    CoverageJudge,
    ElicitationResult,
    Judges,
    PlacementJudge,
    ProbeJudge,
    anchors_in,
    elicit,
    unanchored_in,
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
    elicited_fact_id,
)

DAY = date(2026, 9, 8)


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


GYM = {"uid": "a-gym", "name": "gym"}
DINNER = {"uid": "a-din", "name": "dinner"}
ROWS_FIXTURE = [
    {"uid": "c-oats", "name": "Oats before gym", "description": "Eat oats two hours before the gym.", "necessity": "must", "anchors": [GYM]},
    {"uid": "c-run", "name": "Run at 18:00", "description": "Run at 18:00 when cooking dinner.", "necessity": "should", "anchors": [GYM, DINNER]},
    {"uid": "c-exit", "name": "Block exit criteria", "description": "Every block ends with a written exit criterion.", "necessity": "must", "anchors": []},
]


def test_placement_targets_are_the_concerns_plus_unplaced() -> None:
    assert PLACEMENT_TARGETS == (*(c.key for c in CONCERNS), "unplaced")
    assert "request" not in PLACEMENT_TARGETS


def test_anchors_in_groups_rows_by_anchor_with_two_example_names() -> None:
    anchors = anchors_in(ROWS_FIXTURE)
    by_uid = {a["uid"]: a for a in anchors}
    assert set(by_uid) == {"a-gym", "a-din"}
    assert by_uid["a-gym"]["name"] == "gym"
    assert by_uid["a-gym"]["example_rules"] == ["Oats before gym", "Run at 18:00"]
    assert by_uid["a-din"]["example_rules"] == ["Run at 18:00"]


def test_unanchored_in_returns_the_rules_with_no_anchor() -> None:
    assert [r["uid"] for r in unanchored_in(ROWS_FIXTURE)] == ["c-exit"]
    assert unanchored_in(ROWS_FIXTURE)[0]["description"].startswith("Every block")


@pytest.mark.asyncio
async def test_placement_maps_every_offered_uid_to_a_row() -> None:
    client = _SchemaOutputClient(
        {
            "anchors": [{"uid": "a-gym", "row": "body"}, {"uid": "a-din", "row": "fixed"}],
            "rules": [{"uid": "c-exit", "row": "method"}],
        }
    )
    placement = await PlacementJudge(client).place(
        anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
    )
    assert placement.anchors == {"a-gym": "body", "a-din": "fixed"}
    assert placement.rules == {"c-exit": "method"}
    sent = json.loads(client.calls[0][0][1].content)
    assert [a["uid"] for a in sent["anchors"]] == ["a-gym", "a-din"]
    assert [r["uid"] for r in sent["rules"]] == ["c-exit"]
    assert [c["key"] for c in sent["rows"]] == list(PLACEMENT_TARGETS)


@pytest.mark.asyncio
async def test_placement_refuses_a_uid_it_did_not_offer() -> None:
    client = _SchemaOutputClient(
        {"anchors": [{"uid": "a-gym", "row": "body"}, {"uid": "a-din", "row": "fixed"}, {"uid": "a-ghost", "row": "body"}], "rules": [{"uid": "c-exit", "row": "method"}]}
    )
    with pytest.raises(ValueError, match="a-ghost"):
        await PlacementJudge(client).place(
            anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_placement_refuses_to_leave_an_offered_uid_unplaced() -> None:
    client = _SchemaOutputClient({"anchors": [{"uid": "a-gym", "row": "body"}], "rules": []})
    with pytest.raises(ValueError, match="a-din"):
        await PlacementJudge(client).place(
            anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_placement_with_nothing_to_place_makes_no_call() -> None:
    client = _SchemaOutputClient()
    placement = await PlacementJudge(client).place(anchors=[], unanchored_rules=[], session_key="C1:1.0")
    assert placement.anchors == {} and placement.rules == {}
    assert client.calls == []


@pytest.mark.asyncio
async def test_classify_sends_names_only_for_a_criterion_that_does_not_need_the_text() -> None:
    client = _SchemaOutputClient({"status": "uncovered", "why": "no duration"})
    cell = CellRef(row="body", criterion="tacit_knowledge")
    state, why = await CoverageJudge(client).classify(
        cell=cell,
        rules=[{"name": "Oats before gym", "necessity": "must", "description": "SHOULD NOT BE SENT"}],
        stated=["gym at 18:00"],
        request="deep work in the morning, gym at 18:00",
        session_key="C1:1.0",
    )
    assert state == "uncovered"
    assert why == "no duration"
    sent = json.loads(client.calls[0][0][1].content)
    assert sent["row"]["key"] == "body"
    assert sent["criterion"]["key"] == "tacit_knowledge"
    assert sent["rules"] == [{"name": "Oats before gym", "necessity": "must"}]
    assert sent["stated"] == ["gym at 18:00"]
    assert sent["request"] == "deep work in the morning, gym at 18:00"


@pytest.mark.asyncio
async def test_classify_sends_the_rule_text_for_a_contradiction_or_an_ambiguity() -> None:
    """A contradiction lives in what a rule says. Asked with names only, the
    judge called "work from 08:00" against a 09:30 `must` not-contradictory
    5/5 (2026-09-05)."""
    for criterion in ("contradictory", "unclear"):
        client = _SchemaOutputClient({"status": "uncovered", "why": "clashes"})
        await CoverageJudge(client).classify(
            cell=CellRef(row="fixed", criterion=criterion),
            rules=[{"name": "Work start time", "necessity": "must", "description": "Work starts at 09:30 on arrival."}],
            stated=["deep work runs 08:00 to 09:30 today"],
            request=None,
            session_key="C1:1.0",
        )
        sent = json.loads(client.calls[0][0][1].content)
        assert sent["rules"] == [
            {"name": "Work start time", "necessity": "must", "description": "Work starts at 09:30 on arrival."}
        ], criterion


@pytest.mark.asyncio
async def test_classify_refuses_a_status_outside_the_schema() -> None:
    client = _SchemaOutputClient({"status": "maybe", "why": ""})
    with pytest.raises(ValueError):
        await CoverageJudge(client).classify(
            cell=CellRef(row="body", criterion="unclear"), rules=[], stated=[], request=None, session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_generate_returns_a_draft_with_host_minted_option_ids() -> None:
    client = _SchemaOutputClient(
        {"grounded": True, "question": "How long is the gym?", "why_needed": "to place it", "options": ["60 min", "90 min"]}
    )
    cell = CellRef(row="body", criterion="tacit_knowledge")
    draft = await ProbeJudge(client).generate(
        cell=cell,
        rules_full=[{"name": "Oats before gym", "necessity": "must", "description": "Eat oats two hours before the gym."}],
        conversation=["deep work in the morning, gym at 18:00"],
        request="deep work in the morning, gym at 18:00",
        session_key="C1:1.0",
    )
    assert draft is not None
    assert draft.cell_id == cell.id
    assert draft.question == "How long is the gym?"
    assert draft.why_needed == "to place it"
    assert [o.option_id for o in draft.options] == ["elicit.body.tacit_knowledge:1", "elicit.body.tacit_knowledge:2"]
    assert [o.label for o in draft.options] == ["60 min", "90 min"]
    sent = json.loads(client.calls[0][0][1].content)
    assert sent["rules"][0]["description"].startswith("Eat oats")


@pytest.mark.asyncio
async def test_generate_may_return_nothing() -> None:
    client = _SchemaOutputClient({"grounded": False, "question": None, "why_needed": None, "options": []})
    draft = await ProbeJudge(client).generate(
        cell=CellRef(row="movement", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
    )
    assert draft is None


@pytest.mark.asyncio
async def test_generate_refuses_grounded_without_a_question() -> None:
    client = _SchemaOutputClient({"grounded": True, "question": None, "why_needed": None, "options": []})
    with pytest.raises(ValueError, match="grounded"):
        await ProbeJudge(client).generate(
            cell=CellRef(row="movement", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_generate_refuses_more_than_four_options() -> None:
    client = _SchemaOutputClient({"grounded": True, "question": "Which?", "why_needed": "w", "options": ["a", "b", "c", "d", "e"]})
    with pytest.raises(ValueError, match="at most four"):
        await ProbeJudge(client).generate(
            cell=CellRef(row="body", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
        )


class _StubPlacement:
    def __init__(self, placement: dict[str, str], rules: dict[str, str] | None = None) -> None:
        self._placement = placement
        self._rules = rules or {}
        self.calls = 0

    async def place(self, *, anchors, unanchored_rules, session_key):  # noqa: ANN001
        from fateforger.agents.timeboxing.elicitation_judges import Placement

        self.calls += 1
        return Placement(anchors=self._placement, rules=self._rules)


class _StubCoverage:
    """`table` maps cell id -> state; anything else answers `covered`."""

    def __init__(self, table: dict[str, str]) -> None:
        self.table = table
        self.asked: list[str] = []

    async def classify(self, *, cell, rules, stated, request, session_key):  # noqa: ANN001
        self.asked.append(cell.id)
        return self.table.get(cell.id, "covered"), "stub"


class _StubProbe:
    """`grounded` is the set of cell ids that get a draft."""

    def __init__(self, grounded: set[str]) -> None:
        self.grounded = grounded
        self.asked: list[str] = []
        #: (cell id, the conversation handed over, the uids of the row's rules)
        self.seen: list[tuple[str, list[str], list[str]]] = []

    async def generate(self, *, cell, rules_full, conversation, request, session_key):  # noqa: ANN001
        from fateforger.agents.timeboxing.session_contracts import ProbeDraft

        self.asked.append(cell.id)
        self.seen.append((cell.id, list(conversation), [str(r["uid"]) for r in rules_full]))
        if cell.id not in self.grounded:
            return None
        return ProbeDraft(cell_id=cell.id, question=f"about {cell.id}?", why_needed="stub")


def _snapshot(*facts: PlanningFact, assumptions: list[PlannerAssumption] | None = None) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=DAY, timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING),
        facts=[
            PlanningFact(fact_id="request-1", kind=FactKind.REQUESTED_ACTIVITY, value="deep work in the morning, gym at 18:00", source="user"),
            *facts,
        ],
        assumptions=list(assumptions or []),
    )


PLACED = {"a-gym": "body", "a-din": "fixed"}


def _judges(coverage: dict[str, str], grounded: set[str] | None = None, placement: dict[str, str] = PLACED):
    return Judges(
        placement=_StubPlacement(placement, {"c-exit": "method"}),
        coverage=_StubCoverage(coverage),
        probe=_StubProbe(grounded if grounded is not None else set()),
    )


def _run(snapshot, judges, rows=ROWS_FIXTURE) -> ElicitationResult:
    return asyncio.run(elicit(snapshot, rows, judges, session_key="C1:1.0"))


def test_rows_with_no_rules_and_nothing_stated_are_not_applicable_without_a_call() -> None:
    judges = _judges({})
    result = _run(_snapshot(), judges)
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    # movement, fragile, not_today, unplaced: no rules placed, nothing stated
    for row in ("movement", "fragile", "not_today", "unplaced"):
        for criterion in ("tacit_assumptions", "alternatives", "unclear", "contradictory", "tacit_knowledge"):
            assert matrix.cells[f"elicit.{row}.{criterion}"] == "not_applicable"
    assert not any(cell.startswith("elicit.movement.") for cell in judges.coverage.asked)
    # body (gym rules), fixed (dinner), method (exit criteria), request (stated): classified
    assert any(cell.startswith("elicit.body.") for cell in judges.coverage.asked)
    assert any(cell.startswith("elicit.method.") for cell in judges.coverage.asked)
    assert any(cell.startswith("elicit.request.") for cell in judges.coverage.asked)


def test_the_matrix_fact_is_written_whole_at_the_stable_id_with_placement() -> None:
    result = _run(_snapshot(), _judges({}))
    assert result.matrix_fact.fact_id == coverage_fact_id(DAY)
    assert result.matrix_fact.kind is FactKind.COVERAGE_MATRIX
    assert result.matrix_fact.source == "system"
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert set(matrix.cells) == {c.id for c in ALL_CELLS}
    assert matrix.placement == PLACED
    assert matrix.rule_placement == {"c-exit": "method"}
    assert matrix.placed_against == ["a-din", "a-gym", "c-exit"]
    assert matrix.rows["body"].rule_count == 2 and matrix.rows["body"].must_count == 1
    assert matrix.rows["method"].rule_count == 1
    assert matrix.rows["request"].stated == 1


def test_placement_is_reused_when_the_uid_set_is_unchanged_and_redone_when_it_moves() -> None:
    judges = _judges({})
    first = _run(_snapshot(), judges)
    assert judges.placement.calls == 1
    again = _run(_snapshot(first.matrix_fact), judges)
    assert judges.placement.calls == 1
    matrix = CoverageMatrix.model_validate(again.matrix_fact.value)
    assert matrix.placement == PLACED
    fewer = [row for row in ROWS_FIXTURE if row["uid"] != "c-exit"]
    asyncio.run(elicit(_snapshot(first.matrix_fact), fewer, judges, session_key="C1:1.0"))
    assert judges.placement.calls == 2


def test_a_cell_already_covered_is_not_classified_again() -> None:
    judges = _judges({"elicit.body.unclear": "uncovered"})
    first = _run(_snapshot(), judges)
    asked_first = set(judges.coverage.asked)
    assert "elicit.body.tacit_knowledge" in asked_first
    judges.coverage.asked.clear()
    _run(_snapshot(first.matrix_fact), judges)
    assert "elicit.body.tacit_knowledge" not in judges.coverage.asked  # was covered
    assert "elicit.body.unclear" in judges.coverage.asked  # still open, re-asked


def test_probes_come_from_the_top_three_ranked_cells_and_ungroundable_ones_stay_uncovered() -> None:
    open_cells = {
        "elicit.body.unclear": "uncovered",
        "elicit.body.tacit_knowledge": "uncovered",
        "elicit.fixed.unclear": "uncovered",
        "elicit.request.unclear": "uncovered",
    }
    judges = _judges(open_cells, grounded={"elicit.body.tacit_knowledge", "elicit.fixed.unclear"})
    result = _run(_snapshot(), judges)
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    # Rank as the orchestrator did before it learned which cells ground:
    # the final matrix already sorts the ungroundable cell last.
    ranked = ranked_open_cells(matrix.model_copy(update={"unaskable": []}))
    assert judges.probe.asked == [c.id for c in ranked[:3]]
    assert [p.cell_id for p in result.probes] == [c.id for c in ranked[:3] if c.id in judges.probe.grounded]
    ungrounded = [c.id for c in ranked[:3] if c.id not in judges.probe.grounded]
    assert ungrounded and set(ungrounded) <= set(matrix.unaskable)
    for cell in ungrounded:
        assert matrix.cells[cell] == "uncovered"


def test_a_cell_the_user_assumed_past_is_not_generated_for() -> None:
    assumed = PlannerAssumption(assumption_id="as-1", requirement_id="elicit.body.unclear", value="fine", why_needed="w", filed_by="user")
    judges = _judges({"elicit.body.unclear": "uncovered"}, grounded={"elicit.body.unclear"})
    result = _run(_snapshot(assumptions=[assumed]), judges)
    assert judges.probe.asked == []
    assert result.probes == []


def test_a_cell_already_answered_is_not_generated_for_again() -> None:
    cell = "elicit.body.unclear"
    answered = PlanningFact(
        fact_id=elicited_fact_id(cell), kind=FactKind.ELICITED_STATEMENT,
        value={"cell": cell, "text": "the gym is 75 minutes"}, source="user",
    )
    judges = _judges({cell: "uncovered"}, grounded={cell})
    result = _run(_snapshot(answered), judges)
    assert judges.probe.asked == []
    assert result.probes == []


def test_stated_facts_reach_the_classifier_and_the_generator() -> None:
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:30"}, source="user")
    said = PlanningFact(fact_id=elicited_fact_id("elicit.body.unclear"), kind=FactKind.ELICITED_STATEMENT, value={"cell": "elicit.body.unclear", "text": "gym is 75 minutes"}, source="user")

    class _Recording(_StubCoverage):
        def __init__(self, table: dict[str, str] | None = None) -> None:
            super().__init__(table or {})
            self.stated: list[list[str]] = []

        async def classify(self, *, cell, rules, stated, request, session_key):  # noqa: ANN001
            self.stated.append(list(stated))
            return await super().classify(cell=cell, rules=rules, stated=stated, request=request, session_key=session_key)

    # `said` answers `elicit.body.unclear`, which closes it; the open cell the
    # generator gets is a different one on the same row, so the statement still
    # reaches the classifier and the conversation without being re-asked.
    coverage = _Recording({"elicit.body.tacit_knowledge": "uncovered"})
    probe = _StubProbe({"elicit.body.tacit_knowledge"})
    judges = Judges(placement=_StubPlacement(PLACED, {"c-exit": "method"}), coverage=coverage, probe=probe)
    result = _run(_snapshot(frame, said), judges)
    assert coverage.stated and all("gym is 75 minutes" in s for s in coverage.stated)
    assert all(any("07:00" in line for line in s) for s in coverage.stated)
    assert probe.seen, "the generator was never called"
    assert "elicit.body.unclear" not in probe.asked  # answered, so never re-asked
    cell_id, conversation, rule_uids = probe.seen[0]
    assert cell_id == "elicit.body.tacit_knowledge"
    assert "deep work in the morning, gym at 18:00" in conversation  # the request leads
    assert "gym is 75 minutes" in conversation  # the elicited statement
    assert any("07:00" in line for line in conversation)  # the frame line
    assert set(rule_uids) == {"c-oats", "c-run"}  # the body row's rules, full
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert matrix.rows["body"].stated == 1
    assert matrix.rows["bounded"].stated == 1


def test_a_suspended_rule_is_not_placed_or_counted() -> None:
    suspended = PlanningFact(fact_id="suspend:c-run", kind=FactKind.SUSPENDED_CONSTRAINT, value={"uid": "c-run", "reason": "not today"}, source="user")
    result = _run(_snapshot(suspended), _judges({}))
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert matrix.rows["body"].rule_count == 1
    assert "a-din" not in matrix.placed_against


def test_one_failing_classify_fails_the_turn_and_writes_nothing() -> None:
    class _Broken(_StubCoverage):
        async def classify(self, *, cell, rules, stated, request, session_key):  # noqa: ANN001
            if cell.id == "elicit.body.unclear":
                raise ValueError("model returned garbage")
            return "covered", "stub"

    judges = Judges(placement=_StubPlacement(PLACED, {"c-exit": "method"}), coverage=_Broken({}), probe=_StubProbe(set()))
    with pytest.raises(ValueError, match="garbage"):
        _run(_snapshot(), judges)


def test_elicit_needs_a_locked_day() -> None:
    bare = _snapshot().model_copy(update={"planning_day": None})
    with pytest.raises(ValueError, match="locked"):
        _run(bare, _judges({}))
