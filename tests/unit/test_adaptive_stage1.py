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
    ArtifactApproval,
    ArtifactDraft,
    ArtifactKind,
    PlanningArtifact,
    AwaitingUser,
    ConfirmPlanningDay,
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
    TurnFailed,
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
                    payload={
                        "day_label": "Tuesday",
                        "groups": [
                            {"name": "Day", "items": [{"text": "memo", "source": "user"}]}
                        ],
                    },
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
    kernel, _, _ = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), RestoreConstraint(constraint_uid="c-gym"))
    assert isinstance(outcome, TurnFailed) and outcome.code == "stale_restore"


def test_a_suspension_fact_with_no_uid_is_refused_not_ignored() -> None:
    """Absence must not read as data: a malformed suspension naming no rule
    must not silently read as no suspension at all."""
    bad = PlanningFact(
        fact_id=suspension_fact_id("c-gym"),
        kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"reason": "not today"},
        source="user",
    )
    snapshot = _snapshot(stage1="closed", facts=[*_snapshot().facts, bad])
    kernel, _, _ = _kernel(snapshot)
    with pytest.raises(ValueError) as excinfo:
        _turn(kernel, snapshot, Advance())
    assert bad.fact_id in str(excinfo.value)


def test_file_assumption_is_recorded_as_the_users_and_closes_the_question() -> None:
    cell = ALL_CELLS[0]
    snapshot = _snapshot(facts=[*_snapshot().facts, _matrix_fact(cell.id)])
    kernel, repository, planner = _kernel(snapshot)
    _turn(kernel, snapshot, Advance())
    held = _load(repository)
    outcome = _turn(
        kernel, held,
        FileAssumption(
            requirement_id=cell.id,
            value="assume a normal day",
            why_needed="user forced past",
        ),
    )
    after = _load(repository)
    [assumption] = after.assumptions
    assert assumption.filed_by == "user" and assumption.requirement_id == cell.id
    assert after.pending_blocker is None

    # Forced past, not forced past for one turn: the same cell must not come
    # back on the very next Advance, and the planner must actually run.
    assert isinstance(outcome, GateMet)
    consented = _turn(kernel, after, Advance())
    reasked = (
        isinstance(consented, AwaitingUser) and consented.requirement_id == cell.id
    )
    assert not reasked
    assert _load(repository).pending_blocker is None
    assert len(planner.briefs) == 1


def test_file_assumption_for_the_last_open_cell_proposes_to_close_before_any_blocker() -> None:
    """`FileAssumption` falls through to Stage 1, which now runs before the
    hard-blocker check: forcing past the last open cell proposes to close,
    and the missing request is asked after consent, not instead of the stage."""
    cell = ALL_CELLS[0]
    snapshot = _snapshot(
        facts=[
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME,
                         value={"wake": "07:00", "sleep": "23:30"}, source="user"),
            _matrix_fact(cell.id),
        ]
    )
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot,
                    FileAssumption(requirement_id=cell.id, value="assume a normal day", why_needed="user forced past"))
    assert isinstance(outcome, GateMet)
    assert _load(repository).stage1 == "proposed"
    assert planner.briefs == []


def test_the_shape_of_the_day_is_asked_before_the_priorities_question() -> None:
    """An auto-started session has said nothing. Stage 1 asks about the day's
    shape first; "what do you want out of the day" waits until it closes.
    Ruled 2026-09-09 (#411): the ladder is 1, 1, …, 2, 3 by construction."""
    cell = ALL_CELLS[0]
    snapshot = _snapshot(
        facts=[
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME,
                         value={"wake": "07:00", "sleep": "23:30"}, source="user"),
            _matrix_fact(cell.id),          # one Stage 1 cell open, no request
        ]
    )
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.gate is not None
    assert planner.briefs == []


def test_the_priorities_question_is_still_asked_once_stage_one_closes() -> None:
    """The hard user blocker is guaranteed before a skeleton; it is only asked
    later, not never."""
    snapshot = _snapshot(
        stage1="closed",
        facts=[
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME,
                         value={"wake": "07:00", "sleep": "23:30"}, source="user"),
        ],
    )
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.requested_activity"
    assert outcome.gate is None
    assert planner.briefs == []


def test_a_missing_frame_with_no_rule_to_probe_it_is_asked_after_stage_one_closes() -> None:
    """With nothing on record about the frame and no request, Stage 1 has no
    row to ground a probe in; it proposes to close, and the frame question
    comes from the catalog after consent rather than being lost."""
    snapshot = _snapshot(facts=[_matrix_fact(None)])   # nothing open, no frame, no request
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, GateMet)
    assert _load(repository).stage1 == "proposed"
    outcome = _turn(kernel, _load(repository), Advance())       # consent
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.requested_activity"


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
    kernel, _, _ = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), DenyAssumption(assumption_id="nope"))
    assert isinstance(outcome, TurnFailed) and outcome.code == "stale_assumption"


def test_back_from_a_proposal_returns_to_the_day_card() -> None:
    """Stage 1 has no rung of its own to back out to.

    A re-presented Stage 1 with nothing newly stated is the same proposal
    `_stage1_outcome` already made, and a probe already answered is never
    re-asked -- so with no skeleton yet, Back is the planning-day rung: it
    clears `planning_day` and resets `stage1` to `"open"`, same as backing
    out of any other stage-two question this early.
    """
    kernel, repository, _ = _kernel(_snapshot(stage1="proposed"))
    _turn(kernel, _snapshot(stage1="proposed"), GoBack())
    after = _load(repository)
    assert after.planning_day is None
    assert after.stage1 == "open"


def test_file_assumption_against_a_stage_two_requirement_lets_the_planner_run() -> None:
    """`skeleton.day_frame` is catalog stage 1 too, but it is not an elicitation
    cell -- filing against it must never answer with the Stage 1 card, closed
    or not."""
    snapshot = _snapshot(stage1="closed")
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(
        kernel, snapshot,
        FileAssumption(
            requirement_id="skeleton.day_frame",
            value={"wake": "07:00", "sleep": "23:00"},
            why_needed="already on record; filed anyway",
        ),
    )
    assert not isinstance(outcome, GateMet)
    assert len(planner.briefs) == 1


def test_a_day_frame_assumption_does_not_skip_the_hard_activity_blocker() -> None:
    """`stage_of` alone reads `skeleton.day_frame` as "stage 1" too (the
    five-rung card grouping); membership in the cell ids is what tells
    them apart. Get that wrong and this assumption skips straight past a
    still-missing hard blocker to a Stage 1 verdict it has no business
    making. `stage1="closed"` so this exercises the blocker path itself,
    not Stage 1's own (now-earlier) gate."""
    snapshot = _snapshot(stage1="closed", facts=[])
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(
        kernel, snapshot,
        FileAssumption(
            requirement_id="skeleton.day_frame",
            value={"wake": "07:00", "sleep": "23:00"},
            why_needed="filed anyway",
        ),
    )
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.requested_activity"
    assert outcome.gate is None
    assert planner.briefs == []


def test_file_assumption_against_an_unknown_requirement_is_refused() -> None:
    kernel, _, _ = _kernel(_snapshot())
    outcome = _turn(
        kernel, _snapshot(),
        FileAssumption(
            requirement_id="not.a.real.requirement", value="x", why_needed="y"
        ),
    )
    assert isinstance(outcome, TurnFailed) and outcome.code == "unknown_requirement"


def test_back_to_the_day_card_and_reconfirm_reopens_stage_one() -> None:
    """Stage 1 is not permanently skipped just because a day was cleared and
    re-picked."""
    snapshot = _snapshot(stage1="closed")
    kernel, repository, planner = _kernel(snapshot)
    _turn(kernel, snapshot, GoBack())
    after_back = _load(repository)
    assert after_back.planning_day is None
    assert after_back.stage1 == "open"

    reconfirmed = _turn(
        kernel, after_back,
        ConfirmPlanningDay(
            planning_day=PlanningDay.lock_default(
                value=DAY,
                timezone="Europe/Amsterdam",
                lock_revision=1,
                day_type=DayType.WORKING,
            )
        ),
    )
    # Without the fix, stage1 stayed "closed" across the reconfirm and this
    # turn would have gone straight to the planner instead of re-proposing.
    assert isinstance(reconfirmed, GateMet)
    assert planner.briefs == []
    _turn(kernel, _load(repository), Advance())
    assert len(planner.briefs) == 1


class _StagedContext:
    """What the host actually does: only the Stage 1 resolve counts suspensions.

    `_frame_from_corpus` calls `count_suspended`; the candidate resolve reads
    the calendar and the rules and never asks for the count. Both return rows.
    """

    def __init__(self) -> None:
        self.targets: list[ArtifactKind] = []

    async def propose_planning_day(self, request):
        raise AssertionError("day is locked in these tests")

    async def resolve(self, snapshot, *, target, progress):
        self.targets.append(target)
        if target is ArtifactKind.SKELETON:
            return PlanningContext(
                applicable_constraints=ROWS, suspended_constraint_count=7
            )
        return PlanningContext(
            facts=[
                PlanningFact(
                    fact_id=f"calendar:{DAY.isoformat()}",
                    kind=FactKind.CALENDAR_SNAPSHOT,
                    value={"ok": True, "events": []},
                    source="system",
                ),
                PlanningFact(
                    fact_id=f"constraints:{DAY.isoformat()}",
                    kind=FactKind.ACTIVE_CONSTRAINTS,
                    value={"count": len(ROWS)},
                    source="system",
                ),
                PlanningFact(
                    fact_id="placements-1",
                    kind=FactKind.CONCRETE_PLACEMENTS,
                    value={"count": 1},
                    source="system",
                ),
            ],
            applicable_constraints=ROWS,
            calendar_snapshot={"ok": True, "events": []},
        )


class _CandidatePlanner:
    def __init__(self) -> None:
        self.briefs = []

    async def produce(self, brief, progress):
        self.briefs.append(brief)
        return PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.VALIDATED_CANDIDATE,
                    payload={"events": [{"summary": "Gym", "start": "17:00"}]},
                    dependency_revisions={"skeleton": 1},
                )
            ]
        )


def _approved_skeleton_snapshot() -> PlanningSessionSnapshot:
    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={
            "day_label": "Tuesday",
            "groups": [{"name": "Day", "items": [{"text": "memo", "source": "user"}]}],
        },
        dependency_revisions={"planning_day": 1},
    )
    return _snapshot(
        # Stage 1 consent is given; this test is about what follows.
        stage1="closed",
        artifacts=[skeleton],
        approvals=[
            ArtifactApproval(
                artifact_id=skeleton.artifact_id,
                artifact_revision=skeleton.revision,
                artifact_digest=skeleton.digest,
                actor_user_id="U1",
                session_revision=1,
            )
        ],
        suspended_constraint_count=7,
    )


def test_a_candidate_turn_keeps_the_count_stage_one_wrote() -> None:
    """Catches the count being reset to zero on every turn after Stage 1.

    The candidate resolve knows the rows and not the count. While the kernel
    wrote both together, its default of 0 overwrote what Stage 1 had measured,
    and the card's "12 working-day rules off because today is a vacation day"
    line silently became "0" one turn after it was true.
    """

    snapshot = _approved_skeleton_snapshot()
    repository = InMemoryPlanningSessionRepository([snapshot])
    context = _StagedContext()
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=_CandidatePlanner(),
        context=context,
        commit=_Commit(),
    )
    _turn(kernel, snapshot, Advance())

    assert context.targets == [ArtifactKind.VALIDATED_CANDIDATE]
    after = _load(repository)
    assert [row["uid"] for row in after.applicable_constraints] == ["c-gym", "c-plan"]
    assert after.suspended_constraint_count == 7


def test_a_resolve_that_did_not_count_leaves_the_last_count_alone() -> None:
    """`None` is "this resolve did not look", which is not the answer zero."""

    snapshot = _snapshot(stage1="proposed", suspended_constraint_count=7)
    repository = InMemoryPlanningSessionRepository([snapshot])

    class _RowsOnly:
        async def propose_planning_day(self, request):
            raise AssertionError("day is locked in these tests")

        async def resolve(self, snapshot, *, target, progress):
            return PlanningContext(applicable_constraints=ROWS)

    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=_Planner(),
        context=_RowsOnly(),
        commit=_Commit(),
    )
    _turn(kernel, snapshot, Advance())

    after = _load(repository)
    assert [row["uid"] for row in after.applicable_constraints] == ["c-gym", "c-plan"]
    assert after.suspended_constraint_count == 7


def test_a_planning_context_carries_probe_drafts_and_they_never_reach_the_snapshot() -> None:
    from fateforger.agents.timeboxing.session_contracts import ProbeDraft

    probe = ProbeDraft(cell_id="elicit.body.unclear", question="How long is the gym?", why_needed="body")
    context = PlanningContext(probes=[probe])
    assert context.probes[0].cell_id == "elicit.body.unclear"
    assert "probes" not in PlanningSessionSnapshot.model_fields


class _ProbingContext(_Context):
    """The host that resolved a probe for a cell this turn."""

    def __init__(self, matrix_fact, probes) -> None:
        self._fact = matrix_fact
        self._probes = probes

    async def resolve(self, snapshot, *, target, progress):
        return PlanningContext(
            facts=[self._fact],
            applicable_constraints=ROWS,
            suspended_constraint_count=3,
            probes=self._probes,
        )


def _probing_kernel(snapshot, matrix_fact, probes):
    repository = InMemoryPlanningSessionRepository([snapshot])
    planner = _Planner()
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=_ProbingContext(matrix_fact, probes),
        commit=_Commit(),
    )
    return kernel, repository, planner


def test_the_resolved_probe_is_asked_instead_of_the_catalog_text() -> None:
    from fateforger.agents.timeboxing.session_contracts import BlockerOption, ProbeDraft

    cell = ALL_CELLS[0]
    probe = ProbeDraft(
        cell_id=cell.id,
        question="Still up at 07:00 on Tuesday?",
        why_needed="to bound the morning",
        options=[BlockerOption(option_id=f"{cell.id}:1", label="yes", effect="yes")],
    )
    kernel, repository, _ = _probing_kernel(_snapshot(), _matrix_fact(cell.id), [probe])
    outcome = _turn(kernel, _snapshot(), Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.question == "Still up at 07:00 on Tuesday?"
    assert outcome.why_needed == "to bound the morning"
    assert [o.option_id for o in outcome.options] == [f"{cell.id}:1"]
    held = _load(repository).pending_blocker
    assert held.requirement_id == cell.id and [o.option_id for o in held.options] == [f"{cell.id}:1"]
    assert all(
        f.kind is not FactKind.COVERAGE_MATRIX or "probes" not in str(f.value)
        for f in _load(repository).facts
    )


def test_a_probe_for_another_cell_falls_back_to_the_catalog_text() -> None:
    from fateforger.agents.timeboxing.session_contracts import ProbeDraft

    cell = ALL_CELLS[0]
    other = ProbeDraft(cell_id=ALL_CELLS[1].id, question="other?", why_needed="w")
    kernel, _, _ = _probing_kernel(_snapshot(), _matrix_fact(cell.id), [other])
    outcome = _turn(kernel, _snapshot(), Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.question != "other?"
    assert outcome.options == []


def test_a_pressed_option_files_the_same_fact_a_typed_answer_does() -> None:
    """A press and a typed reply must reach the store as one shape.

    `closed_cells` closes a cell on `value["cell"]` and the judges read the
    answer out of `value["text"]`. The press once filed
    `{requirement_id, label, effect}` instead, so a button-answered cell was
    never closed, its answer never reached `CoverageJudge` or `ProbeJudge`,
    and an empty string entered the conversation the next probe was written
    against -- the cell came back every turn to the cap.
    """
    from fateforger.agents.timeboxing.elicitation import closed_cells
    from fateforger.agents.timeboxing.session_contracts import (
        BlockerOption,
        ChooseBlockerOption,
        ProbeDraft,
    )

    cell = ALL_CELLS[0]
    probe = ProbeDraft(
        cell_id=cell.id,
        question="Still up at 07:00 on Tuesday?",
        why_needed="to bound the morning",
        options=[
            BlockerOption(
                option_id=f"{cell.id}:1", label="07:00", effect="to bound the morning"
            )
        ],
    )
    kernel, repository, _ = _probing_kernel(_snapshot(), _matrix_fact(cell.id), [probe])
    _turn(kernel, _snapshot(), Advance())
    held = _load(repository)

    _turn(
        kernel,
        held,
        ChooseBlockerOption(requirement_id=cell.id, option_id=f"{cell.id}:1"),
    )
    answered = _load(repository)

    # (a) the stored shape
    [statement] = [f for f in answered.facts if f.kind is FactKind.ELICITED_STATEMENT]
    assert statement.value == {"cell": cell.id, "text": "07:00"}
    assert statement.source == "user"

    # (b) the cell is closed
    assert cell.id in closed_cells(answered)

    # (c) the next turn does not re-ask it. The context keeps handing back a
    # matrix that still calls the cell uncovered, so the answer is the only
    # thing that can stop the re-ask.
    outcome = _turn(kernel, answered, Advance())
    reasked = isinstance(outcome, AwaitingUser) and outcome.requirement_id == cell.id
    assert not reasked


def test_the_typed_path_files_that_same_shape_against_the_pending_cell() -> None:
    """The other half of the seam: `_typed_facts` binds a typed answer to the
    held cell, which is why the press had to be made to match it and not the
    other way round. Recorded as a known limit: any elicited statement while
    a cell is held binds to that cell, so an off-topic reply closes the cell
    that was asked."""
    from fateforger.agents.timeboxing.session_contracts import PendingBlocker
    from fateforger.slack_bot.timeboxing_intents import (
        ElicitedStatementDraft,
        InterpretedTimeboxTurn,
        _typed_facts,
    )

    cell = ALL_CELLS[0]
    snapshot = _snapshot(
        pending_blocker=PendingBlocker(
            requirement_id=cell.id,
            fact_kind=FactKind.ELICITED_STATEMENT,
            options=[],
        )
    )
    [typed] = _typed_facts(
        InterpretedTimeboxTurn(
            decision="provide_facts",
            facts=[ElicitedStatementDraft(kind="elicited_statement", value="07:00")],
        ),
        snapshot,
    )
    assert typed.kind is FactKind.ELICITED_STATEMENT
    assert typed.value == {"cell": cell.id, "text": "07:00"}
