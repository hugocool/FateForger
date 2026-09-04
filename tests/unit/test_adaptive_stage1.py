"""Stage 1 in the kernel: propose to close, wait for consent, re-open on facts.

The planner is a fake that records the brief it was given, the context port
returns the rows a host would, and no model is anywhere. Every assertion is on
outcomes and snapshot fields this system minted.
"""
from __future__ import annotations

import asyncio
import itertools
from datetime import date

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    InMemoryPlanningSessionRepository,
    PlanningContext,
    TurnRequest,
)
from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CoverageMatrix
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactDraft,
    ArtifactKind,
    AwaitingUser,
    DayType,
    DenyAssumption,
    FactKind,
    FileAssumption,
    GateMet,
    GoBack,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    RestoreConstraint,
    coverage_fact_id,
    elicited_fact_id,
    suspension_fact_id,
)

DAY = date(2026, 9, 8)
ROWS = [
    {"uid": "c-gym", "name": "Oats before gym", "necessity": "must", "anchors": [{"uid": "a1", "name": "gym"}]},
    {"uid": "c-plan", "name": "Plan at 17:00", "necessity": "should", "anchors": []},
]


class _Planner:
    def __init__(self) -> None:
        self.briefs = []

    async def produce(self, brief, progress):
        self.briefs.append(brief)
        return PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.SKELETON,
                    payload={"markdown": "## Tuesday"},
                    dependency_revisions={"planning_day": 1},
                )
            ]
        )


class _Context:
    async def propose_planning_day(self, request):
        raise AssertionError("day is locked in these tests")

    async def resolve(self, snapshot, *, target, progress):
        return PlanningContext(applicable_constraints=ROWS, suspended_constraint_count=3)


class _Commit:
    async def commit(self, candidate, *, digest):
        raise AssertionError("no commit in Stage 1")


class _Sink:
    async def emit(self, event):
        return None


def _snapshot(**update) -> PlanningSessionSnapshot:
    base = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=DAY, timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=[
            PlanningFact(fact_id="activity-1", kind=FactKind.REQUESTED_ACTIVITY, value="deep work", source="user"),
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:30"}, source="user"),
        ],
    )
    return base.model_copy(update=update)


def _kernel(snapshot: PlanningSessionSnapshot):
    repository = InMemoryPlanningSessionRepository([snapshot])
    planner = _Planner()
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=_Context(),
        commit=_Commit(),
    )
    return kernel, repository, planner


_interaction_ids = itertools.count(1)


def _turn(kernel, snapshot, intent):
    # Each call is one real user interaction, and the kernel's replay guard
    # keys a stored outcome on (session_key, interaction_id): reusing one
    # value across the several turns a test drives silently replayed the
    # first turn's outcome for every later one, never reaching the planner.
    # A fresh id per call is what a real bridge would send.
    interaction_id = f"1.{next(_interaction_ids)}"
    return asyncio.run(
        kernel.turn(
            TurnRequest(
                session_key=snapshot.session_key,
                interaction_id=interaction_id,
                actor_user_id="U1",
                expected_revision=snapshot.revision,
                intent=intent,
            ),
            progress=_Sink(),
        )
    )


def _load(repository, key="C1:1.0"):
    return asyncio.run(repository.load_or_create(key, owner_user_id="U1"))


def _matrix_fact(open_cell_id: str | None):
    cells = {c.id: "not_applicable" for c in ALL_CELLS}
    if open_cell_id:
        cells[open_cell_id] = "uncovered"
    return PlanningFact(
        fact_id=coverage_fact_id(DAY),
        kind=FactKind.COVERAGE_MATRIX,
        value=CoverageMatrix(cells=cells).model_dump(mode="json"),
        source="system",
    )


def test_a_locked_day_with_no_open_cells_proposes_to_close_and_plans_nothing() -> None:
    kernel, repository, planner = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), Advance())
    assert isinstance(outcome, GateMet)
    assert outcome.gate.open_cells == []
    assert outcome.gate.day_label == "working Tuesday"
    assert planner.briefs == []
    current = _load(repository)
    assert current.stage1 == "proposed"
    assert [row["uid"] for row in current.applicable_constraints] == ["c-gym", "c-plan"]
    assert current.suspended_constraint_count == 3


def test_consent_is_the_next_advance_and_then_the_planner_runs() -> None:
    kernel, repository, planner = _kernel(_snapshot(stage1="proposed"))
    _turn(kernel, _snapshot(stage1="proposed"), Advance())
    assert _load(repository).stage1 == "closed"
    assert len(planner.briefs) == 1


def test_an_open_cell_is_asked_with_the_gate_attached() -> None:
    cell = ALL_CELLS[0]
    snapshot = _snapshot(facts=[*_snapshot().facts, _matrix_fact(cell.id)])
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.gate is not None and [c.id for c in outcome.gate.open_cells] == [cell.id]
    assert _load(repository).pending_blocker.requirement_id == cell.id
    assert planner.briefs == []


def test_an_elicited_statement_after_a_proposal_re_opens_the_stage() -> None:
    kernel, repository, _ = _kernel(_snapshot(stage1="proposed"))
    fact = PlanningFact(
        fact_id=elicited_fact_id(None), kind=FactKind.ELICITED_STATEMENT,
        value={"cell": None, "text": "dentist at 15:00"}, source="user",
    )
    outcome = _turn(kernel, _snapshot(stage1="proposed"), ProvidePlanningFacts(facts=[fact]))
    assert isinstance(outcome, GateMet)  # no judge yet, so nothing is open; but the stage was re-evaluated
    assert _load(repository).stage1 == "proposed"


def test_a_stage_two_fact_after_a_proposal_is_consent() -> None:
    kernel, repository, planner = _kernel(_snapshot(stage1="proposed"))
    fact = PlanningFact(fact_id="activity-2", kind=FactKind.REQUESTED_ACTIVITY, value="gym at 18:00", source="user")
    _turn(kernel, _snapshot(stage1="proposed"), ProvidePlanningFacts(facts=[fact]))
    assert _load(repository).stage1 == "closed"
    assert len(planner.briefs) == 1


def test_a_suspended_rule_reaches_the_card_but_not_the_brief() -> None:
    kernel, repository, planner = _kernel(_snapshot(stage1="proposed"))
    suspend = PlanningFact(
        fact_id=suspension_fact_id("c-gym"), kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    _turn(kernel, _snapshot(stage1="proposed"), ProvidePlanningFacts(facts=[suspend]))
    reopened = _load(repository)
    assert reopened.stage1 == "proposed"
    assert [row["uid"] for row in reopened.applicable_constraints] == ["c-gym", "c-plan"]
    _turn(kernel, reopened, Advance())
    [brief] = planner.briefs
    assert [row["uid"] for row in brief.applicable_constraints] == ["c-plan"]


def test_restore_deletes_the_suspension_and_reopens_the_stage() -> None:
    suspend = PlanningFact(
        fact_id=suspension_fact_id("c-gym"), kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    snapshot = _snapshot(stage1="proposed", facts=[*_snapshot().facts, suspend])
    kernel, repository, _ = _kernel(snapshot)
    _turn(kernel, snapshot, RestoreConstraint(constraint_uid="c-gym"))
    after = _load(repository)
    assert not any(f.kind is FactKind.SUSPENDED_CONSTRAINT for f in after.facts)
    assert after.stage1 == "proposed"  # re-evaluated in the same turn; nothing open, proposed again


def test_restore_of_a_rule_not_suspended_is_refused() -> None:
    from fateforger.agents.timeboxing.session_contracts import TurnFailed

    kernel, _, _ = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), RestoreConstraint(constraint_uid="c-gym"))
    assert isinstance(outcome, TurnFailed) and outcome.code == "stale_restore"


def test_file_assumption_is_recorded_as_the_users_and_closes_the_question() -> None:
    cell = ALL_CELLS[0]
    snapshot = _snapshot(facts=[*_snapshot().facts, _matrix_fact(cell.id)])
    kernel, repository, _ = _kernel(snapshot)
    _turn(kernel, snapshot, Advance())
    held = _load(repository)
    _turn(kernel, held, FileAssumption(requirement_id=cell.id, value="assume a normal day", why_needed="user forced past"))
    after = _load(repository)
    [assumption] = after.assumptions
    assert assumption.filed_by == "user" and assumption.requirement_id == cell.id
    assert after.pending_blocker is None


def test_deny_removes_the_assumption_and_reopens_the_stage() -> None:
    cell = ALL_CELLS[0]
    snapshot = _snapshot(facts=[*_snapshot().facts, _matrix_fact(cell.id)])
    kernel, repository, _ = _kernel(snapshot)
    _turn(kernel, snapshot, Advance())
    _turn(kernel, _load(repository), FileAssumption(requirement_id=cell.id, value="x", why_needed="y"))
    [assumption] = _load(repository).assumptions
    _turn(kernel, _load(repository), DenyAssumption(assumption_id=assumption.assumption_id))
    after = _load(repository)
    assert after.assumptions == []
    assert after.stage1 == "open"


def test_deny_of_an_unknown_assumption_is_refused() -> None:
    from fateforger.agents.timeboxing.session_contracts import TurnFailed

    kernel, _, _ = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), DenyAssumption(assumption_id="nope"))
    assert isinstance(outcome, TurnFailed) and outcome.code == "stale_assumption"


def test_back_from_a_proposal_reopens_stage_one() -> None:
    kernel, repository, _ = _kernel(_snapshot(stage1="proposed"))
    _turn(kernel, _snapshot(stage1="proposed"), GoBack())
    assert _load(repository).stage1 == "open"
