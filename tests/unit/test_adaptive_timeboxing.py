from __future__ import annotations

import asyncio
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
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactApproval,
    ArtifactDraft,
    ArtifactKind,
    AwaitingApproval,
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
            _fact("gym-1", FactKind.GYM, True),
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
                requirement_id="skeleton.gym_placement",
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
            payload={"candidate_digest": digest, "status": "committed"},
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
            payload={"candidate_digest": digest, "status": "committed"},
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
                requirement_id="skeleton.gym_placement",
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
                _fact("gym-placement-1", FactKind.GYM_PLACEMENT, "16:00"),
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
