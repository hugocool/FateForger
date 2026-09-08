"""Doubles for the adaptive-timeboxing kernel's ports.

The unit suite and the incident replay drive the same kernel, so they take the
same doubles from here. A second recording sink with slightly different
semantics is how two tests come to disagree about what "was emitted" means.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

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
    ArtifactSnapshot,
    DayType,
    ArtifactKind,
    FactKind,
    PlanningArtifact,
    PlanningBrief,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
)


def locked_day() -> PlanningDay:
    """The planning day the kernel suites share, already locked."""
    return PlanningDay.lock_default(
        value=date(2026, 8, 29),
        timezone="Europe/Amsterdam",
        lock_revision=1,
    )


class RecordedContextPort(PlanningContextPort):
    """A context port that counts its calls and answers with fixed facts."""

    def __init__(self, *, facts: tuple[PlanningFact, ...] = ()) -> None:
        self.facts = facts
        self.proposal_calls = 0
        self.resolve_calls = 0

    async def propose_planning_day(self, request: TurnRequest) -> PlanningDay:
        self.proposal_calls += 1
        return locked_day()

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


class RecordingProgressSink(ProgressSink):
    """Keeps every emitted event in order."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def emit(self, event: object) -> None:
        self.events.append(event)


def _fact(fact_id: str, kind: FactKind, value: object) -> PlanningFact:
    return PlanningFact(
        fact_id=fact_id,
        kind=kind,
        value=value,
        source="user",
        source_interaction_id="1772.1",
    )


class ForbiddenCommitPort(CommitPort):
    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        raise AssertionError("commit must not be called")


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


def _incident_snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=locked_day(),
        facts=[
            _fact("activity-1", FactKind.REQUESTED_ACTIVITY, "Plan Saturday"),
            _fact("gym-1", FactKind.REQUESTED_ACTIVITY, True),
            _fact("frame-1", FactKind.DAY_FRAME, {"wake": "08:00", "sleep": "23:30"}),
        ],
        # Stage 1 consent is given; this test is about what follows.
        stage1="closed",
    )


def weekend_locked_day() -> PlanningDay:
    """The Saturday the planner suites replay, locked at revision 3."""
    return PlanningDay(
        date=date(2026, 8, 29),
        timezone="Europe/Amsterdam",
        iso_weekday=6,
        day_type=DayType.WEEKEND,
        classification_basis="calendar",
        lock_revision=3,
    )

def input_brief(*, facts: list[PlanningFact] | None = None) -> PlanningBrief:
    return PlanningBrief(
        session_key="C206:1777651200.0",
        base_revision=7,
        observed_at=datetime(2000, 1, 1, tzinfo=UTC),
        locked_day=weekend_locked_day(),
        facts=facts
        or [
            PlanningFact(
                fact_id="fact-supermarket",
                kind=FactKind.REQUESTED_ACTIVITY,
                value={"activity": "supermarket"},
                source="user",
                source_interaction_id="1777651201.0",
            ),
            PlanningFact(
                fact_id="fact-gym",
                kind=FactKind.REQUESTED_ACTIVITY,
                value={"requested": True},
                source="user",
                source_interaction_id="1777651202.0",
            ),
        ],
        assumptions=[],
        current_artifacts=[
            ArtifactSnapshot(
                artifact_id="day-frame-1",
                kind=ArtifactKind.DAY_FRAME,
                revision=2,
                digest="a" * 64,
                payload={"work_window": ["09:00", "17:30"]},
            ),
            ArtifactSnapshot(
                artifact_id="inputs-1",
                kind=ArtifactKind.CAPTURED_INPUTS,
                revision=4,
                digest="b" * 64,
                payload={"activities": ["supermarket", "gym"]},
            ),
        ],
        approvals=[],
        applicable_constraints={"stale": "must be replaced"},
        calendar_snapshot={"stale": "must be replaced"},
        target_artifact=ArtifactKind.SKELETON,
        readiness={"target_artifact": "skeleton"},
        allowed_outputs={ArtifactKind.SKELETON},
    )


def brief(target: ArtifactKind) -> PlanningBrief:
    return PlanningBrief(
        session_key="C1:1.0",
        base_revision=1,
        observed_at=datetime(2026, 8, 31, tzinfo=UTC),
        locked_day=PlanningDay(
            date=date(2026, 8, 31),
            timezone="Europe/Amsterdam",
            iso_weekday=1,
            day_type=DayType.WORKING,
            classification_basis="calendar",
            lock_revision=1,
        ),
        facts=[],
        assumptions=[],
        current_artifacts=[],
        approvals=[],
        applicable_constraints=[],
        calendar_snapshot={},
        target_artifact=target,
        readiness={},
        allowed_outputs=set(),
    )
