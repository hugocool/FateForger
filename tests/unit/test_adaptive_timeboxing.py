from __future__ import annotations

import asyncio
import logging
from datetime import date

import pytest

from fateforger.agents.timeboxing import adaptive_timeboxing as adaptive_module
from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    CommitPort,
    InMemoryPlanningSessionRepository,
    PlannerPort,
    PlanningContext,
    PlanningContextPort,
    ProgressSink,
    TurnRequest,
)
from fateforger.agents.timeboxing.readiness import (
    ArtifactRequirement,
    ReadinessGap,
    ReadinessReport,
    RequirementOwner,
    TimeboxRequirements,
)
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactApproval,
    ArtifactDraft,
    ArtifactKind,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    ChooseBlockerOption,
    Committed,
    ConfirmPlanningDay,
    FactKind,
    PlannerAssumptionDraft,
    PlanningArtifact,
    PlanningBrief,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    TurnFailed,
    UserBlockerDraft,
)


def _fact(fact_id: str, kind: FactKind, value: object) -> PlanningFact:
    return PlanningFact(
        fact_id=fact_id,
        kind=kind,
        value=value,
        source="user",
        source_interaction_id="1772.1",
    )


def _locked_day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 8, 29),
        timezone="Europe/Amsterdam",
        lock_revision=1,
    )


def _incident_snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
        facts=[
            _fact("activity-1", FactKind.REQUESTED_ACTIVITY, "Plan Saturday"),
            _fact("gym-1", FactKind.REQUESTED_ACTIVITY, True),
            _fact("frame-1", FactKind.DAY_FRAME, {"wake": "08:00", "sleep": "23:30"}),
        ],
    )


def _skeleton(*, revision: int = 1) -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=revision,
        payload={"markdown": "## Saturday\n- 17:00 Gym"},
        dependency_revisions={"planning_day": 1},
    )


def _candidate() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload={"events": [{"summary": "Gym", "start": "17:00"}]},
        dependency_revisions={"skeleton": 1},
    )


def _approval(
    artifact: PlanningArtifact, *, session_revision: int = 3
) -> ArtifactApproval:
    return ArtifactApproval(
        artifact_id=artifact.artifact_id,
        artifact_revision=artifact.revision,
        artifact_digest=artifact.digest,
        actor_user_id="U1",
        session_revision=session_revision,
    )


def _skeleton_result() -> PlanningResult:
    return PlanningResult(
        artifact_updates=[
            ArtifactDraft(
                kind=ArtifactKind.SKELETON,
                payload={"markdown": "## Saturday\n- 17:00 Gym"},
                dependency_revisions={"planning_day": 1},
            )
        ],
        assumptions=[
            PlannerAssumptionDraft(
                requirement_id="skeleton.ordinary_placement",
                value="17:00",
                why_needed="place gym around the dinner anchor",
                invalidated_by=["gym", "dinner"],
            )
        ],
    )


class RecordedPlanner(PlannerPort):
    def __init__(self, result: PlanningResult) -> None:
        self.result = result
        self.briefs: list[PlanningBrief] = []

    @property
    def calls(self) -> int:
        return len(self.briefs)

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        self.briefs.append(brief)
        return self.result


class BlockingPlanner(PlannerPort):
    def __init__(self, result: PlanningResult) -> None:
        self.result = result
        self.calls = 0
        self.first_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        self.calls += 1
        self.first_entered.set()
        await self.release.wait()
        return self.result


class RecordedContextPort(PlanningContextPort):
    def __init__(self, *, facts: tuple[PlanningFact, ...] = ()) -> None:
        self.facts = facts
        self.proposal_calls = 0
        self.resolve_calls = 0

    async def propose_planning_day(self, request: TurnRequest) -> PlanningDay:
        self.proposal_calls += 1
        return _locked_day()

    async def resolve(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        target: ArtifactKind,
        progress: ProgressSink,
    ) -> PlanningContext:
        self.resolve_calls += 1
        return PlanningContext(
            facts=list(self.facts),
            applicable_constraints={"items": []},
            calendar_snapshot={"events": []},
        )


class ForbiddenCommitPort(CommitPort):
    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        raise AssertionError("commit must not be called")


class RecordedCommitPort(CommitPort):
    def __init__(self) -> None:
        self.digests: list[str] = []

    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        self.digests.append(digest)
        return PlanningArtifact.create(
            artifact_id="receipt-1",
            kind=ArtifactKind.COMMIT_RECEIPT,
            revision=1,
            payload={"candidate_digest": digest, "committed": True, "tx_id": "tx-1"},
            dependency_revisions={"validated_candidate": candidate.revision},
        )


class BlockingCommitPort(CommitPort):
    def __init__(self) -> None:
        self.calls = 0
        self.digests: list[str] = []
        self.first_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        self.calls += 1
        self.digests.append(digest)
        self.first_entered.set()
        await self.release.wait()
        return PlanningArtifact.create(
            artifact_id="receipt-1",
            kind=ArtifactKind.COMMIT_RECEIPT,
            revision=1,
            payload={"candidate_digest": digest, "committed": True, "tx_id": "tx-1"},
            dependency_revisions={"validated_candidate": candidate.revision},
        )


class RecordingProgressSink(ProgressSink):
    def __init__(self) -> None:
        self.events: list[object] = []

    async def emit(self, event: object) -> None:
        self.events.append(event)


class ExplodingProgressSink(ProgressSink):
    async def emit(self, event: object) -> None:
        raise RuntimeError("raw progress payload must stay private")


def _kernel(
    repo: InMemoryPlanningSessionRepository,
    planner: PlannerPort,
    *,
    context: RecordedContextPort | None = None,
    commit: CommitPort | None = None,
) -> AdaptiveTimeboxing:
    return AdaptiveTimeboxing(
        repository=repo,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=context or RecordedContextPort(),
        commit=commit or ForbiddenCommitPort(),
    )


def _advance_request(*, expected_revision: int = 3) -> TurnRequest:
    return TurnRequest(
        session_key="C1:1.0",
        interaction_id="1772.2",
        actor_user_id="U1",
        expected_revision=expected_revision,
        intent=Advance(),
    )


@pytest.mark.asyncio
async def test_advance_with_planner_owned_gaps_returns_skeleton_same_turn() -> None:
    """Catches an advance that recaps instead of producing its required artifact."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(_skeleton_result())

    outcome = await _kernel(repo, planner).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_missing_planning_day_returns_day_approval_without_planning() -> None:
    """Catches date-sensitive planning before the host locks a planning day."""

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=0, owner_user_id="U1"
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    planner = RecordedPlanner(_skeleton_result())
    context = RecordedContextPort()

    outcome = await _kernel(repo, planner, context=context).turn(
        _advance_request(expected_revision=0), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.PLANNING_DAY
    assert planner.calls == 0
    assert context.proposal_calls == 1


@pytest.mark.asyncio
async def test_planner_owned_blocker_is_rejected_as_illegal_user_blocker() -> None:
    """Catches planner-owned gym placement being delegated back to the user."""

    result = PlanningResult(
        blockers=[
            UserBlockerDraft(
                requirement_id="skeleton.ordinary_placement",
                why_needed="ask the user for an exact gym time",
            )
        ]
    )
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])

    outcome = await _kernel(repo, RecordedPlanner(result)).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "illegal_user_blocker"


@pytest.mark.asyncio
async def test_prose_only_planner_result_is_missing_required_artifact() -> None:
    """Catches a successful-looking completion with no typed skeleton."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])

    outcome = await _kernel(repo, RecordedPlanner(PlanningResult())).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "missing_required_artifact"


@pytest.mark.asyncio
async def test_duplicate_interaction_replays_outcome_without_second_planner_call() -> None:
    """Catches duplicate Slack delivery causing a second planner side effect."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(_skeleton_result())
    kernel = _kernel(repo, planner)
    request = _advance_request()

    first = await kernel.turn(request, progress=RecordingProgressSink())
    replay = await kernel.turn(request, progress=RecordingProgressSink())

    assert replay == first
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_advance_invokes_planner_at_most_once() -> None:
    """Catches concurrent duplicate delivery passing the replay gate twice."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = BlockingPlanner(_skeleton_result())
    kernel = _kernel(repo, planner)
    request = _advance_request()

    first_task = asyncio.create_task(
        kernel.turn(request, progress=RecordingProgressSink())
    )
    await planner.first_entered.wait()
    duplicate_task = asyncio.create_task(
        kernel.turn(request, progress=RecordingProgressSink())
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    planner.release.set()
    first, duplicate = await asyncio.gather(first_task, duplicate_task)

    assert first == duplicate
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_stale_expected_revision_fails_without_planning() -> None:
    """Catches a stale card or harness result overwriting a newer session."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(_skeleton_result())

    outcome = await _kernel(repo, planner).turn(
        _advance_request(expected_revision=2), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "stale_session_revision"
    assert planner.calls == 0


@pytest.mark.asyncio
async def test_unapproved_skeleton_cannot_reach_candidate_planning() -> None:
    """Catches candidate production that bypasses exact skeleton approval."""

    skeleton = _skeleton()
    snapshot = _incident_snapshot().model_copy(update={"artifacts": [skeleton]})
    repo = InMemoryPlanningSessionRepository([snapshot])
    planner = RecordedPlanner(
        PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.VALIDATED_CANDIDATE,
                    payload={"events": []},
                )
            ]
        )
    )

    outcome = await _kernel(repo, planner).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact == skeleton
    assert planner.calls == 0


@pytest.mark.asyncio
async def test_candidate_approval_commits_exact_digest_once() -> None:
    """Catches candidate approval bypassing or duplicating the commit executor."""

    skeleton = _skeleton()
    candidate = _candidate()
    snapshot = _incident_snapshot().model_copy(
        update={
            "artifacts": [skeleton, candidate],
            "approvals": [_approval(skeleton)],
        }
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    commit = RecordedCommitPort()
    request = TurnRequest(
        session_key="C1:1.0",
        interaction_id="1772.3",
        actor_user_id="U1",
        expected_revision=3,
        intent=ApproveArtifact(
            artifact_id=candidate.artifact_id,
            artifact_revision=candidate.revision,
            artifact_digest=candidate.digest,
        ),
    )
    kernel = _kernel(repo, RecordedPlanner(PlanningResult()), commit=commit)

    outcome = await kernel.turn(request, progress=RecordingProgressSink())
    replay = await kernel.turn(request, progress=RecordingProgressSink())

    assert isinstance(outcome, Committed)
    assert replay == outcome
    assert commit.digests == [candidate.digest]


@pytest.mark.asyncio
async def test_concurrent_duplicate_candidate_approval_commits_at_most_once() -> None:
    """Catches concurrent duplicate approval invoking external commit twice."""

    skeleton = _skeleton()
    candidate = _candidate()
    snapshot = _incident_snapshot().model_copy(
        update={
            "artifacts": [skeleton, candidate],
            "approvals": [_approval(skeleton)],
        }
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    commit = BlockingCommitPort()
    kernel = _kernel(repo, RecordedPlanner(PlanningResult()), commit=commit)
    request = TurnRequest(
        session_key="C1:1.0",
        interaction_id="1772.concurrent-commit",
        actor_user_id="U1",
        expected_revision=3,
        intent=ApproveArtifact(
            artifact_id=candidate.artifact_id,
            artifact_revision=candidate.revision,
            artifact_digest=candidate.digest,
        ),
    )

    first_task = asyncio.create_task(
        kernel.turn(request, progress=RecordingProgressSink())
    )
    await commit.first_entered.wait()
    duplicate_task = asyncio.create_task(
        kernel.turn(request, progress=RecordingProgressSink())
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    commit.release.set()
    first, duplicate = await asyncio.gather(first_task, duplicate_task)

    assert first == duplicate
    assert commit.calls == 1
    assert commit.digests == [candidate.digest]


@pytest.mark.asyncio
async def test_relocking_day_removes_changed_and_downstream_approvals() -> None:
    """Catches approvals surviving replacement of their artifact or ancestor."""

    old_day = PlanningArtifact.create(
        artifact_id="planning-day-1",
        kind=ArtifactKind.PLANNING_DAY,
        revision=1,
        payload=_locked_day().model_dump(mode="json"),
        dependency_revisions={},
    )
    skeleton = _skeleton()
    candidate = _candidate()
    snapshot = _incident_snapshot().model_copy(
        update={
            "artifacts": [old_day, skeleton, candidate],
            "approvals": [
                _approval(old_day),
                _approval(skeleton),
                _approval(candidate),
            ],
        }
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    request = TurnRequest(
        session_key="C1:1.0",
        interaction_id="1772.relock",
        actor_user_id="U1",
        expected_revision=3,
        intent=ConfirmPlanningDay(
            planning_day=PlanningDay.lock_default(
                value=date(2026, 8, 30),
                timezone="Europe/Amsterdam",
                lock_revision=4,
            )
        ),
    )

    await _kernel(repo, RecordedPlanner(_skeleton_result())).turn(
        request, progress=RecordingProgressSink()
    )
    restored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    current_day = max(
        (
            artifact
            for artifact in restored.artifacts
            if artifact.kind is ArtifactKind.PLANNING_DAY
        ),
        key=lambda artifact: artifact.revision,
    )

    assert {approval.artifact_id for approval in restored.approvals} == {
        current_day.artifact_id
    }
    assert current_day.artifact_id != old_day.artifact_id


@pytest.mark.asyncio
async def test_blocker_for_satisfied_requirement_is_invalid_planner_result() -> None:
    """Catches a satisfied user fact being reopened instead of producing the artifact."""

    result = PlanningResult(
        artifact_updates=_skeleton_result().artifact_updates,
        blockers=[
            UserBlockerDraft(
                requirement_id="skeleton.requested_activity",
                why_needed="ask for an activity again",
            )
        ],
    )
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])

    outcome = await _kernel(repo, RecordedPlanner(result)).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_planner_result"


@pytest.mark.asyncio
async def test_assumption_for_satisfied_requirement_is_invalid_planner_result() -> None:
    """Catches an assumption overriding a fact that already satisfies readiness."""

    snapshot = _incident_snapshot().model_copy(
        update={
            "facts": [
                *_incident_snapshot().facts,
                _fact("gym-placement-1", FactKind.ORDINARY_PLACEMENT, "16:00"),
            ]
        }
    )
    repo = InMemoryPlanningSessionRepository([snapshot])

    outcome = await _kernel(repo, RecordedPlanner(_skeleton_result())).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_planner_result"


def test_valid_user_blocker_cannot_hide_later_satisfied_requirement_blocker() -> None:
    """Catches first-blocker return skipping validation of the complete result."""

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
    )
    requirements = TimeboxRequirements()
    readiness = requirements.evaluate(ArtifactKind.SKELETON, snapshot)
    result = PlanningResult(
        artifact_updates=_skeleton_result().artifact_updates,
        blockers=[
            UserBlockerDraft(
                requirement_id="skeleton.requested_activity",
                why_needed="an activity is genuinely missing",
            ),
            UserBlockerDraft(
                requirement_id="skeleton.locked_day",
                why_needed="incorrectly reopen the locked day",
            ),
        ],
    )
    kernel = _kernel(
        InMemoryPlanningSessionRepository([snapshot]),
        RecordedPlanner(PlanningResult()),
    )

    _, outcome = kernel._apply_planning_result(
        snapshot,
        ArtifactKind.SKELETON,
        readiness,
        result,
        "U1",
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_planner_result"


def test_valid_user_blocker_cannot_hide_satisfied_requirement_assumption() -> None:
    """Catches blocker outcome selection skipping assumption validation."""

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
        facts=[
            _fact(
                "ordinary-placement-1",
                FactKind.ORDINARY_PLACEMENT,
                "morning",
            )
        ],
    )
    requirements = TimeboxRequirements()
    readiness = requirements.evaluate(ArtifactKind.SKELETON, snapshot)
    result = PlanningResult(
        artifact_updates=_skeleton_result().artifact_updates,
        blockers=[
            UserBlockerDraft(
                requirement_id="skeleton.requested_activity",
                why_needed="an activity is genuinely missing",
            )
        ],
        assumptions=[
            PlannerAssumptionDraft(
                requirement_id="skeleton.ordinary_placement",
                value="afternoon",
                why_needed="incorrectly override the existing placement",
            )
        ],
    )
    kernel = _kernel(
        InMemoryPlanningSessionRepository([snapshot]),
        RecordedPlanner(PlanningResult()),
    )

    _, outcome = kernel._apply_planning_result(
        snapshot,
        ArtifactKind.SKELETON,
        readiness,
        result,
        "U1",
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_planner_result"


@pytest.mark.asyncio
async def test_non_owner_is_rejected_before_planner_side_effect() -> None:
    """Catches a different actor advancing a restored owner's session."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(_skeleton_result())
    request = _advance_request().model_copy(update={"actor_user_id": "U2"})

    outcome = await _kernel(repo, planner).turn(
        request, progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "session_owner_mismatch"
    assert planner.calls == 0


@pytest.mark.asyncio
async def test_non_owner_is_rejected_before_approval_or_commit_side_effect() -> None:
    """Catches a different actor approving and committing the owner's candidate."""

    skeleton = _skeleton()
    candidate = _candidate()
    snapshot = _incident_snapshot().model_copy(
        update={
            "artifacts": [skeleton, candidate],
            "approvals": [_approval(skeleton)],
        }
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    commit = RecordedCommitPort()
    request = TurnRequest(
        session_key="C1:1.0",
        interaction_id="1772.wrong-owner",
        actor_user_id="U2",
        expected_revision=3,
        intent=ApproveArtifact(
            artifact_id=candidate.artifact_id,
            artifact_revision=candidate.revision,
            artifact_digest=candidate.digest,
        ),
    )

    outcome = await _kernel(
        repo, RecordedPlanner(PlanningResult()), commit=commit
    ).turn(request, progress=RecordingProgressSink())
    restored = await repo.load_or_create("C1:1.0", owner_user_id="U1")

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "session_owner_mismatch"
    assert commit.digests == []
    assert restored.approvals == [_approval(skeleton)]


@pytest.mark.asyncio
async def test_repository_raises_public_stale_session_revision() -> None:
    """Catches the SQL adapter being forced to depend on a private exception."""

    snapshot = _incident_snapshot()
    repo = InMemoryPlanningSessionRepository([snapshot])

    with pytest.raises(adaptive_module.StaleSessionRevision):
        await repo.save(
            snapshot,
            expected_revision=2,
            interaction_id="1772.stale-save",
            outcome=TurnFailed(code="unused", message="unused"),
        )


@pytest.mark.asyncio
async def test_progress_failure_does_not_change_saved_domain_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Catches observational progress failure suppressing the planning result."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(_skeleton_result())

    outcome = await _kernel(repo, planner).turn(
        _advance_request(), progress=ExplodingProgressSink()
    )
    replay = await _kernel(repo, planner).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, AwaitingApproval)
    assert replay == outcome
    assert any(
        getattr(record, "error_type", None) == "RuntimeError"
        for record in caplog.records
    )
    assert "raw progress payload must stay private" not in caplog.text


# -- typed options against an under-determined day -------------------------

_DAY_SHAPE = ArtifactRequirement(
    requirement_id="skeleton.day_shape",
    target_artifact=ArtifactKind.SKELETON,
    satisfied_by=(FactKind.ORDINARY_PLACEMENT,),
    owner=RequirementOwner.USER,
    hard=False,
    why_needed="the free afternoon has several materially different shapes",
    resolution="ask",
    question="Which shape should the afternoon take?",
)


class _ClosedChoiceRequirements(TimeboxRequirements):
    """A catalog holding one soft, user-owned requirement with a closed answer set.

    The shipped catalog has exactly one user-owned ask -- "what do you want to
    get out of the day" -- and that one must never grow buttons, because its
    answer set is not closed. So the option machinery needs a requirement the
    catalog does not have yet; this stands in for the unallocated-time question
    it is being built for.

    Soft, deliberately. A *hard* user gap short-circuits before the planner
    runs, and the planner is the only party that can see that three hours have
    two equally workable shapes. An option set is a judgement about this day, so
    it can only come from the turn that looked at this day.
    """

    def evaluate(
        self,
        target_artifact: ArtifactKind,
        snapshot: PlanningSessionSnapshot,
    ) -> ReadinessReport:
        if target_artifact is not ArtifactKind.SKELETON:
            return super().evaluate(target_artifact, snapshot)
        return ReadinessReport(
            target_artifact=target_artifact,
            gaps=(
                ReadinessGap(
                    requirement=_DAY_SHAPE,
                    satisfied=any(
                        fact.kind is FactKind.ORDINARY_PLACEMENT
                        for fact in snapshot.facts
                    ),
                ),
            ),
        )


class _ScriptedPlanner(PlannerPort):
    def __init__(self, *results: PlanningResult) -> None:
        self._results = list(results)
        self.calls = 0

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        self.calls += 1
        return self._results.pop(0)


def _shape_options() -> list[BlockerOption]:
    return [
        BlockerOption(
            option_id="option-1",
            label="Deep work first",
            effect="puts the gym after dinner",
        ),
        BlockerOption(
            option_id="option-2",
            label="Gym first",
            effect="puts deep work in the evening",
        ),
    ]


def _shape_blocker_result() -> PlanningResult:
    return PlanningResult(
        blockers=[
            UserBlockerDraft(
                requirement_id="skeleton.day_shape",
                why_needed="three unallocated hours have two workable shapes",
                options=_shape_options(),
            )
        ]
    )


def _shape_skeleton_result() -> PlanningResult:
    """A skeleton with no assumptions: this catalog has nothing to assume about."""

    return PlanningResult(artifact_updates=_skeleton_result().artifact_updates)


def _choice_request(
    *,
    option_id: str,
    interaction_id: str,
    expected_revision: int,
    requirement_id: str = "skeleton.day_shape",
) -> TurnRequest:
    return TurnRequest(
        session_key="C1:1.0",
        interaction_id=interaction_id,
        actor_user_id="U1",
        expected_revision=expected_revision,
        intent=ChooseBlockerOption(
            requirement_id=requirement_id, option_id=option_id
        ),
    )


def _choice_kernel(
    repo: InMemoryPlanningSessionRepository, planner: PlannerPort
) -> AdaptiveTimeboxing:
    return AdaptiveTimeboxing(
        repository=repo,
        requirements=_ClosedChoiceRequirements(),
        planner=planner,
        context=RecordedContextPort(),
        commit=ForbiddenCommitPort(),
    )


@pytest.mark.asyncio
async def test_offered_options_reach_the_user_with_the_question() -> None:
    """Catches a closed answer set arriving as a question and a blank box.

    Where the alternatives are known, a text answer has to be read back through
    a model to be understood; a press does not. Dropping the options here is the
    difference between those two.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])

    outcome = await _choice_kernel(
        repo, _ScriptedPlanner(_shape_blocker_result())
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.day_shape"
    assert [option.option_id for option in outcome.options] == [
        "option-1",
        "option-2",
    ]


@pytest.mark.asyncio
async def test_a_request_with_no_sleep_facts_and_no_bedtime_rule_blocks_at_capture() -> None:
    """The 2026-09-02 session: one message, no question, a frame the planner assumed.

    "serious c2f work, some finances, ... gym for chest" states what the day is
    for and nothing about when it starts or ends. Nothing in the corpus says so
    either -- the context port resolves no frame. That is a user-owned hard gap,
    so the ladder stops here with the planner uncalled; it does not advance on
    an assumption the user then has to correct after the commit.
    """

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
        facts=[
            _fact(
                "activity-1",
                FactKind.REQUESTED_ACTIVITY,
                "serious c2f work, some finances, gym for chest",
            )
        ],
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    planner = RecordedPlanner(_skeleton_result())
    kernel = _kernel(repo, planner)

    asked = await kernel.turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(asked, AwaitingUser)
    assert asked.requirement_id == "skeleton.day_frame"
    assert planner.calls == 0

    answered = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.3",
            actor_user_id="U1",
            expected_revision=4,
            intent=ProvidePlanningFacts(
                facts=[
                    _fact(
                        "frame-1",
                        FactKind.DAY_FRAME,
                        {"wake": "08:30", "sleep": "00:30"},
                    )
                ]
            ),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(answered, AwaitingApproval)
    assert answered.artifact.kind is ArtifactKind.SKELETON
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_a_planner_that_cannot_read_a_name_may_ask_with_its_readings_as_options() -> None:
    """The typo goes to the user as a question with a proposed reading, not onto the calendar."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    options = [
        BlockerOption(
            option_id="reading-1",
            label="Validate the agent analysis demos",
            effect="the block is titled that way",
        ),
        BlockerOption(
            option_id="reading-2",
            label="Keep it as written",
            effect="the block is titled agent-in-ysis",
        ),
    ]
    planner = RecordedPlanner(
        PlanningResult(
            blockers=[
                UserBlockerDraft(
                    requirement_id="skeleton.activity_reading",
                    why_needed="I cannot read 'agent-in-ysis' as a thing to put on a calendar",
                    options=options,
                )
            ]
        )
    )

    outcome = await _kernel(repo, planner).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.activity_reading"
    assert outcome.options == options
    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert saved.pending_blocker is not None
    assert saved.pending_blocker.fact_kind is FactKind.ACTIVITY_READING


@pytest.mark.asyncio
async def test_a_frame_the_corpus_already_states_is_not_asked_again() -> None:
    """A bedtime rule on record answers the question before it is put."""

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
        facts=[_fact("activity-1", FactKind.REQUESTED_ACTIVITY, "c2f work")],
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    planner = RecordedPlanner(_skeleton_result())
    context = RecordedContextPort(
        facts=(
            PlanningFact(
                fact_id="frame:2026-08-29",
                kind=FactKind.DAY_FRAME,
                value={"wake": "07:30", "sleep": "23:30"},
                source="constraint_memory",
            ),
        )
    )

    outcome = await _kernel(repo, planner, context=context).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_a_planner_assumption_about_the_frame_is_refused() -> None:
    """The frame is the user's; a planner that fills it in has overstepped.

    This is what the 2026-09-02 planner effectively did, filed under a
    requirement id it was allowed to assume. Under the id that names the frame
    the kernel refuses it, so the shape cannot recur by renaming.
    """

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
        facts=[
            _fact("activity-1", FactKind.REQUESTED_ACTIVITY, "c2f work"),
            _fact("frame-1", FactKind.DAY_FRAME, {"wake": "08:30", "sleep": "00:30"}),
        ],
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    planner = RecordedPlanner(
        PlanningResult(
            artifact_updates=_skeleton_result().artifact_updates,
            assumptions=[
                PlannerAssumptionDraft(
                    requirement_id="skeleton.day_frame",
                    value={"wake": "07:00", "sleep": "23:00"},
                    why_needed="a typical day",
                    invalidated_by=["sleep"],
                )
            ],
        )
    )

    outcome = await _kernel(repo, planner).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_planner_result"


@pytest.mark.asyncio
async def test_an_open_question_still_reaches_the_user_and_is_answerable() -> None:
    """The catalog's user-owned asks have no closed answer set, and must not grow one.

    "What do you want to get out of the day?" is answered in Hugo's words, so
    this walks the whole open path: the question goes out with nothing to press,
    the answer arrives as a typed fact, and the next advance produces the
    skeleton it was waiting for. The frame is stated up front so the activity
    is the one open question; the frame's own ask is exercised separately.
    """

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
        facts=[_fact("frame-1", FactKind.DAY_FRAME, {"wake": "08:00", "sleep": "23:30"})],
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    # No assumption: with no gym fact the gym placement requirement is already
    # satisfied, and an assumption settling a settled requirement is its own
    # refusal.
    planner = RecordedPlanner(
        PlanningResult(artifact_updates=_skeleton_result().artifact_updates)
    )
    kernel = _kernel(repo, planner)

    asked = await kernel.turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(asked, AwaitingUser)
    assert asked.requirement_id == "skeleton.requested_activity"
    assert asked.options == []
    assert planner.calls == 0

    answered = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.3",
            actor_user_id="U1",
            expected_revision=4,
            intent=ProvidePlanningFacts(
                facts=[
                    _fact("activity-1", FactKind.REQUESTED_ACTIVITY, "Plan Saturday")
                ]
            ),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(answered, AwaitingApproval)
    assert answered.artifact.kind is ArtifactKind.SKELETON


@pytest.mark.asyncio
async def test_an_open_question_offers_nothing_a_press_could_answer() -> None:
    """Catches a press smuggling an answer past a question that had no options.

    Membership is checked against what the host recorded, so a question that
    offered nothing rejects every press -- including one naming an option id
    that was real on some other turn.
    """

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_locked_day(),
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    kernel = _kernel(repo, RecordedPlanner(_skeleton_result()))

    await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    outcome = await kernel.turn(
        _choice_request(
            option_id="option-1",
            interaction_id="1772.3",
            expected_revision=4,
            requirement_id="skeleton.requested_activity",
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "stale_blocker_choice"


@pytest.mark.asyncio
async def test_choosing_an_offered_option_records_it_and_unblocks_the_turn() -> None:
    """A press has to satisfy the requirement it answered, or it answered nothing."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(_shape_blocker_result(), _shape_skeleton_result())
    kernel = _choice_kernel(repo, planner)

    await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    outcome = await kernel.turn(
        _choice_request(
            option_id="option-2", interaction_id="1772.3", expected_revision=4
        ),
        progress=RecordingProgressSink(),
    )

    restored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    recorded = [
        fact for fact in restored.facts if fact.kind is FactKind.ORDINARY_PLACEMENT
    ]

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON
    assert len(recorded) == 1
    assert recorded[0].source == "user"
    assert recorded[0].value == {
        "requirement_id": "skeleton.day_shape",
        "label": "Gym first",
        "effect": "puts deep work in the evening",
    }
    assert restored.pending_blocker is None


@pytest.mark.asyncio
async def test_an_option_that_was_never_offered_is_refused() -> None:
    """The security-shaped half: a press is a claim about what was on screen.

    Nothing stops a crafted button value naming an option the host never minted,
    so the id is checked against the question the host is actually holding --
    the same reason an approval is checked against an exact artifact digest.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(_shape_blocker_result())
    kernel = _choice_kernel(repo, planner)

    await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    outcome = await kernel.turn(
        _choice_request(
            option_id="option-9", interaction_id="1772.3", expected_revision=4
        ),
        progress=RecordingProgressSink(),
    )

    restored = await repo.load_or_create("C1:1.0", owner_user_id="U1")

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "stale_blocker_choice"
    assert not [
        fact for fact in restored.facts if fact.kind is FactKind.ORDINARY_PLACEMENT
    ]
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_a_refused_press_leaves_the_question_still_askable() -> None:
    """Catches one bad press killing the buttons the user is still looking at.

    A rejection means nothing happened. If it also cleared the pending question,
    a mistyped or replayed value would turn a recoverable refusal into a dead
    end with live-looking buttons and no way to answer.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(_shape_blocker_result(), _shape_skeleton_result())
    kernel = _choice_kernel(repo, planner)

    await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    await kernel.turn(
        _choice_request(
            option_id="option-9", interaction_id="1772.3", expected_revision=4
        ),
        progress=RecordingProgressSink(),
    )
    outcome = await kernel.turn(
        _choice_request(
            option_id="option-1", interaction_id="1772.4", expected_revision=5
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON


@pytest.mark.asyncio
async def test_a_press_against_an_answered_question_is_refused() -> None:
    """Catches an option outliving the question, and answering the one after it.

    The pending record is cleared the moment the turn stops asking, so a second
    press -- a double tap, a stale card someone scrolled back to -- cannot
    re-file an answer against whatever the session moved on to.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(_shape_blocker_result(), _shape_skeleton_result())
    kernel = _choice_kernel(repo, planner)

    await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    await kernel.turn(
        _choice_request(
            option_id="option-1", interaction_id="1772.3", expected_revision=4
        ),
        progress=RecordingProgressSink(),
    )
    outcome = await kernel.turn(
        _choice_request(
            option_id="option-2", interaction_id="1772.4", expected_revision=5
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "stale_blocker_choice"


@pytest.mark.asyncio
async def test_a_typed_answer_takes_the_buttons_down_with_the_question() -> None:
    """Catches an option surviving a question that was answered another way.

    Options are an offer, never an obligation: the user can type past them. When
    they do, the turn moves on and the buttons on the old card are the only
    thing left pointing at the question -- so a press after that must not file a
    second answer against whatever the session has moved on to.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(_shape_blocker_result(), _shape_skeleton_result())
    kernel = _choice_kernel(repo, planner)

    await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    typed = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.3",
            actor_user_id="U1",
            expected_revision=4,
            intent=ProvidePlanningFacts(
                facts=[
                    _fact("shape-1", FactKind.ORDINARY_PLACEMENT, "neither, swim")
                ]
            ),
        ),
        progress=RecordingProgressSink(),
    )
    pressed = await kernel.turn(
        _choice_request(
            option_id="option-1", interaction_id="1772.4", expected_revision=5
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(typed, AwaitingApproval)
    assert isinstance(pressed, TurnFailed)
    assert pressed.code == "stale_blocker_choice"


class RefusedCommitPort(CommitPort):
    """A commit the calendar refused, reported the way tmbx actually reports it."""

    def __init__(self) -> None:
        self.calls = 0

    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        self.calls += 1
        return PlanningArtifact.create(
            artifact_id="receipt-1",
            kind=ArtifactKind.COMMIT_RECEIPT,
            revision=1,
            payload={
                "committed": False,
                "tx_id": None,
                "reason": "malformed_input",
                "candidate_digest": digest,
            },
            dependency_revisions={"validated_candidate": candidate.revision},
        )


class SilentCommitPort(CommitPort):
    """A receipt that does not say whether anything was committed."""

    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        return PlanningArtifact.create(
            artifact_id="receipt-1",
            kind=ArtifactKind.COMMIT_RECEIPT,
            revision=1,
            payload={"candidate_digest": digest},
            dependency_revisions={"validated_candidate": candidate.revision},
        )


async def _session_awaiting_commit(commit: CommitPort):
    """A session whose approved candidate is one approval away from the calendar."""

    skeleton = _skeleton()
    candidate = _candidate()
    snapshot = _incident_snapshot().model_copy(
        update={
            "artifacts": [skeleton, candidate],
            "approvals": [_approval(skeleton)],
        }
    )
    repo = InMemoryPlanningSessionRepository([snapshot])
    kernel = _kernel(repo, RecordedPlanner(PlanningResult()), commit=commit)
    return kernel, snapshot, candidate


@pytest.mark.asyncio
async def test_a_refused_commit_does_not_close_the_session() -> None:
    """Catches a session claiming a calendar it never wrote to.

    Measured live on 2026-08-30: tmbx refused the commit with
    `malformed_input`, the adapter returned a receipt saying
    `"committed": false`, and the kernel marked the session `committed`
    anyway -- because it only checked that the artifact was a COMMIT_RECEIPT,
    never what the receipt said. The session was then terminal, so the day
    could not be committed again, and the calendar was empty.
    """

    commit = RefusedCommitPort()
    kernel, snapshot, candidate = await _session_awaiting_commit(commit)

    outcome = await kernel.turn(
        TurnRequest(
            session_key=snapshot.session_key,
            interaction_id="commit-refused",
            actor_user_id="U1",
            expected_revision=3,
            intent=ApproveArtifact(
                artifact_id=candidate.artifact_id,
                artifact_revision=candidate.revision,
                artifact_digest=candidate.digest,
            ),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "commit_refused"
    stored = await kernel._repository.load_or_create(
        snapshot.session_key, owner_user_id="U1"
    )
    assert stored.status == "open", "a refused commit must leave the day committable"


@pytest.mark.asyncio
async def test_a_receipt_that_says_nothing_is_not_a_commit() -> None:
    """Fails closed: a receipt that cannot say is not evidence that it did."""

    kernel, snapshot, candidate = await _session_awaiting_commit(SilentCommitPort())

    outcome = await kernel.turn(
        TurnRequest(
            session_key=snapshot.session_key,
            interaction_id="commit-silent",
            actor_user_id="U1",
            expected_revision=3,
            intent=ApproveArtifact(
                artifact_id=candidate.artifact_id,
                artifact_revision=candidate.revision,
                artifact_digest=candidate.digest,
            ),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_commit_receipt"
    stored = await kernel._repository.load_or_create(
        snapshot.session_key, owner_user_id="U1"
    )
    assert stored.status == "open"


async def test_a_refused_planner_result_says_which_of_four_rules_it_broke(caplog) -> None:
    """Catches a live failure nobody can diagnose.

    Four different rules answer with `invalid_planner_result`, and the code the
    user sees is deliberately one word. Nothing logged which rule fired, so a
    turn refused in production named a requirement nowhere and left the reader
    guessing between a blocker on a closed requirement, a blocker the user does
    not own, an assumption the planner does not own, and contradictory artifact
    updates.

    The reason is a system-minted code and the ids are the catalog's own, so
    nothing here reaches for user content.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    # An assumption naming a requirement the planner does not own -- the shape a
    # planner reaching for the wrong vocabulary produces.
    planner = RecordedPlanner(
        PlanningResult(
            assumptions=[
                PlannerAssumptionDraft(
                    requirement_id="skeleton.locked_day",
                    value="2026-08-29",
                    why_needed="the day has to be fixed",
                )
            ]
        )
    )

    with caplog.at_level(logging.ERROR):
        outcome = await _kernel(repo, planner).turn(
            _advance_request(), progress=RecordingProgressSink()
        )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_planner_result"
    # Rendered into the message, not passed as `extra`. The first version used
    # `extra=` and this project's formatter drops it, so a live refusal logged
    # the words "planner result refused" and nothing else -- a diagnostic that
    # cannot be read is the failure it was written to prevent.
    refusals = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(refusals) == 1
    assert "reason=assumption_not_planner_owned" in refusals[0]
    assert "requirement_id=skeleton.locked_day" in refusals[0]
    assert "skeleton.ordinary_placement" in refusals[0]


async def test_a_misfiled_assumption_does_not_cost_the_whole_turn(caplog) -> None:
    """A filing error on metadata must not discard a valid artifact.

    Measured live on 2026-08-31. The planner produced a good candidate --
    `plan_read -> plan_apply -> plan_apply -> plan_read -> plan_apply`, three
    attempts converging on something tmbx accepted -- and attached an assumption
    naming `skeleton.ordinary_placement`, a real requirement id from the
    previous stage. The turn was thrown away: one in six draws lost, roughly a
    minute of the user's wait each time, to punish metadata on a working plan.

    An assumption records a judgement and names the requirement it settles.
    Naming the wrong stage's id is a misfiling, not a false claim -- so the
    assumption is dropped and the artifact stands.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(
        PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.SKELETON,
                    payload={"markdown": "## Saturday\n- 17:00 Gym"},
                    dependency_revisions={"planning_day": 1},
                )
            ],
            assumptions=[
                PlannerAssumptionDraft(
                    requirement_id="candidate.concrete_placements",
                    value="17:00",
                    why_needed="a gap from another stage entirely",
                )
            ],
        )
    )

    with caplog.at_level(logging.WARNING):
        outcome = await _kernel(repo, planner).turn(
            _advance_request(), progress=RecordingProgressSink()
        )

    assert not isinstance(outcome, TurnFailed), "the artifact was valid"
    dropped = [r.getMessage() for r in caplog.records if "assumption" in r.getMessage()]
    assert dropped, "dropping it silently would hide a real planner mistake"
    assert "candidate.concrete_placements" in dropped[0]


async def test_an_assumption_over_someone_elses_decision_still_fails(caplog) -> None:
    """The case that must keep failing.

    `skeleton.locked_day` is SYSTEM-owned. A planner asserting it has settled a
    requirement belonging to the user or the system has not misfiled anything --
    it has decided something that was not its to decide, and accepting the
    artifact would act on that guess.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(
        PlanningResult(
            assumptions=[
                PlannerAssumptionDraft(
                    requirement_id="skeleton.locked_day",
                    value="2026-08-29",
                    why_needed="the day has to be fixed",
                )
            ]
        )
    )

    outcome = await _kernel(repo, planner).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "invalid_planner_result"


# --- A committed day is a day that can still be changed (#247) -----------------


def _receipt(*, candidate: PlanningArtifact) -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"candidate_digest": candidate.digest, "committed": True, "tx_id": "tx-1"},
        dependency_revisions={"validated_candidate": candidate.revision},
    )


def _committed_snapshot() -> PlanningSessionSnapshot:
    """The 2026-09-02 session one turn after its commit landed."""

    skeleton = _skeleton()
    candidate = _candidate()
    return _incident_snapshot().model_copy(
        update={
            "revision": 5,
            "artifacts": [skeleton, candidate, _receipt(candidate=candidate)],
            "approvals": [_approval(skeleton), _approval(candidate)],
            "status": "committed",
        }
    )


def _committed_request(intent, *, interaction_id: str = "after-commit") -> TurnRequest:
    return TurnRequest(
        session_key="C1:1.0",
        interaction_id=interaction_id,
        actor_user_id="U1",
        expected_revision=5,
        intent=intent,
    )


@pytest.mark.asyncio
async def test_new_facts_reopen_a_committed_session_without_losing_its_receipt() -> None:
    """Catches the dead thread of 2026-09-02.

    The day was committed at 00:32:01 and the user's correction arrived at
    00:33:25. The session key is the Slack thread, so a session that will not
    take another intent once committed is a thread that is dead for good --
    and the day on the calendar stays wrong. New facts must reopen it, and
    the reopened session must still know it committed: the receipt is the
    record of an external effect, not a derived artifact, and dropping it is
    how a second full commit lands on top of the first (#224).
    """

    repo = InMemoryPlanningSessionRepository([_committed_snapshot()])
    planner = RecordedPlanner(_skeleton_result())

    outcome = await _kernel(repo, planner).turn(
        _committed_request(
            ProvidePlanningFacts(
                facts=[_fact("sleep-1", FactKind.REQUESTED_ACTIVITY, "sleep 00:30-08:30")]
            )
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON
    assert planner.calls == 1
    brief = planner.briefs[0]
    assert brief.target_artifact is ArtifactKind.SKELETON
    assert {a.kind for a in brief.current_artifacts} >= {ArtifactKind.COMMIT_RECEIPT}
    stored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert stored.status == "open"
    kinds = [artifact.kind for artifact in stored.artifacts]
    assert ArtifactKind.COMMIT_RECEIPT in kinds
    assert kinds.count(ArtifactKind.SKELETON) == 1, "the committed skeleton is superseded"
    assert ArtifactKind.VALIDATED_CANDIDATE not in kinds
    assert any(fact.fact_id == "sleep-1" for fact in stored.facts)


@pytest.mark.asyncio
async def test_a_revision_against_the_receipt_reopens_and_records_the_instruction() -> None:
    from fateforger.agents.timeboxing.session_contracts import ReviseArtifact

    snapshot = _committed_snapshot()
    receipt = next(a for a in snapshot.artifacts if a.kind is ArtifactKind.COMMIT_RECEIPT)
    repo = InMemoryPlanningSessionRepository([snapshot])
    planner = RecordedPlanner(_skeleton_result())

    outcome = await _kernel(repo, planner).turn(
        _committed_request(
            ReviseArtifact(
                artifact_id=receipt.artifact_id,
                artifact_revision=receipt.revision,
                artifact_digest=receipt.digest,
                instruction="move all the work two hours later",
            )
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, AwaitingApproval)
    assert planner.calls == 1
    stored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert stored.status == "open"
    instructions = [f for f in stored.facts if f.kind is FactKind.REVISION_INSTRUCTION]
    assert len(instructions) == 1
    assert instructions[0].source == "user"
    assert instructions[0].value == {
        "artifact_kind": "commit_receipt",
        "artifact_id": receipt.artifact_id,
        "instruction": "move all the work two hours later",
    }
    assert any(a.kind is ArtifactKind.COMMIT_RECEIPT for a in stored.artifacts)


@pytest.mark.asyncio
async def test_a_stale_revision_against_a_committed_session_is_refused() -> None:
    from fateforger.agents.timeboxing.session_contracts import ReviseArtifact

    repo = InMemoryPlanningSessionRepository([_committed_snapshot()])
    planner = RecordedPlanner(_skeleton_result())

    outcome = await _kernel(repo, planner).turn(
        _committed_request(
            ReviseArtifact(
                artifact_id="receipt-1",
                artifact_revision=7,
                artifact_digest="0" * 64,
                instruction="move all the work two hours later",
            )
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "stale_revision_target"
    assert planner.calls == 0
    stored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert stored.status == "committed"


@pytest.mark.asyncio
async def test_advance_on_a_committed_session_reports_the_commit_it_made() -> None:
    repo = InMemoryPlanningSessionRepository([_committed_snapshot()])
    planner = RecordedPlanner(_skeleton_result())

    outcome = await _kernel(repo, planner).turn(
        _committed_request(Advance()), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, Committed)
    assert outcome.receipt.artifact_id == "receipt-1"
    assert planner.calls == 0


@pytest.mark.asyncio
async def test_cancelling_a_committed_session_is_refused_with_a_typed_code() -> None:
    """A cancelled status over a written calendar would be a lie about the day."""

    from fateforger.agents.timeboxing.session_contracts import CancelSession

    repo = InMemoryPlanningSessionRepository([_committed_snapshot()])

    outcome = await _kernel(repo, RecordedPlanner(PlanningResult())).turn(
        _committed_request(CancelSession()), progress=RecordingProgressSink()
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "session_committed"
    stored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert stored.status == "committed"


@pytest.mark.asyncio
async def test_a_second_commit_after_reopening_is_a_second_receipt_not_a_replacement() -> None:
    """The reopened day commits again; both receipts stay, in order."""

    skeleton = _skeleton(revision=2)
    candidate = PlanningArtifact.create(
        artifact_id="candidate-2",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=2,
        payload={"events": [{"summary": "Gym", "start": "19:00"}]},
        dependency_revisions={"skeleton": 2},
    )
    first_receipt = _receipt(candidate=_candidate())
    reopened = _incident_snapshot().model_copy(
        update={
            "revision": 8,
            "artifacts": [first_receipt, skeleton, candidate],
            "approvals": [_approval(skeleton)],
            "status": "open",
        }
    )
    repo = InMemoryPlanningSessionRepository([reopened])
    commit = RecordedCommitPort()

    outcome = await _kernel(repo, RecordedPlanner(PlanningResult()), commit=commit).turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="second-commit",
            actor_user_id="U1",
            expected_revision=8,
            intent=ApproveArtifact(
                artifact_id=candidate.artifact_id,
                artifact_revision=candidate.revision,
                artifact_digest=candidate.digest,
            ),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, Committed)
    assert commit.digests == [candidate.digest]
    stored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    receipts = sorted(
        (a for a in stored.artifacts if a.kind is ArtifactKind.COMMIT_RECEIPT),
        key=lambda a: a.revision,
    )
    assert [r.revision for r in receipts] == [1, 2]
    assert len({r.artifact_id for r in receipts}) == 2
    assert outcome.receipt.revision == 2
    assert stored.status == "committed"


# --- The kernel's own catch sites keep the message and the traceback (#249) -----


class _FailingContextPort(RecordedContextPort):
    async def resolve(self, snapshot, *, target, progress):
        raise RuntimeError("no calendar is configured")


@pytest.mark.asyncio
async def test_a_dependency_failure_is_logged_with_its_message_and_traceback(caplog) -> None:
    """`error_type=RuntimeError` names the class of every failure and none of them."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(_skeleton_result())

    with caplog.at_level(logging.ERROR, logger=adaptive_module.__name__):
        outcome = await _kernel(repo, planner, context=_FailingContextPort()).turn(
            _advance_request(), progress=RecordingProgressSink()
        )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "dependency_unavailable"
    record = next(r for r in caplog.records if "context resolution" in r.getMessage())
    assert "no calendar is configured" in record.getMessage()
    assert record.exc_info is not None
