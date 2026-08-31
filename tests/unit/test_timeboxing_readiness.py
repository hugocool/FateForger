from datetime import date

import pytest

from fateforger.agents.timeboxing.readiness import (
    RequirementOwner,
    TimeboxRequirements,
)
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactApproval,
    ArtifactKind,
    FactKind,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)


def _locked_snapshot(*facts: PlanningFact) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 8, 29),
            timezone="Europe/Amsterdam",
            lock_revision=1,
        ),
        facts=list(facts),
    )


def _fact(*, fact_id: str, kind: FactKind, value: object = True) -> PlanningFact:
    return PlanningFact(
        fact_id=fact_id,
        kind=kind,
        value=value,
        source="user",
        source_interaction_id="1.0",
    )


def test_gym_placement_is_planner_owned_and_does_not_block_skeleton() -> None:
    """Catches a regression that asks users to choose an ordinary gym time."""

    snapshot = _locked_snapshot(
        _fact(
            fact_id="activity-1",
            kind=FactKind.REQUESTED_ACTIVITY,
            value="Prepare the presentation",
        ),
        _fact(fact_id="gym-1", kind=FactKind.REQUESTED_ACTIVITY),
    )

    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, snapshot)

    ordinary = report.by_id("skeleton.ordinary_placement")
    gym = report.by_id("skeleton.ordinary_placement")
    assert ordinary.owner is RequirementOwner.PLANNER
    assert ordinary.resolution == "assume"
    assert gym.owner is RequirementOwner.PLANNER
    assert gym.resolution == "assume"
    assert not gym.satisfied
    assert report.first_hard_user_blocker() is None


def test_no_requested_activity_is_a_real_user_blocker() -> None:
    """Catches a regression that drafts a skeleton without any intended activity."""

    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, _locked_snapshot())

    blocker = report.first_hard_user_blocker()
    assert blocker is not None
    assert blocker.requirement_id == "skeleton.requested_activity"
    assert (
        blocker.why_needed == "a skeleton needs at least one intended activity or goal"
    )


def test_candidate_requires_approved_skeleton() -> None:
    """Catches a regression that lets refinement bypass skeleton approval."""

    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=2,
        payload={"markdown": "- Gym at 17:00"},
        dependency_revisions={"planning_day": 1},
    )
    snapshot = _locked_snapshot().model_copy(update={"artifacts": [skeleton]})

    report = TimeboxRequirements().evaluate(ArtifactKind.VALIDATED_CANDIDATE, snapshot)

    approval = report.by_id("candidate.approved_skeleton")
    assert approval.owner is RequirementOwner.SYSTEM
    assert approval.resolution == "validate"
    assert not approval.satisfied


def test_candidate_keeps_missing_system_context_and_placement_with_their_owners() -> (
    None
):
    """Catches a regression that turns fetches or placement decisions into user prompts."""

    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=2,
        payload={"markdown": "- Gym at 17:00"},
        dependency_revisions={"planning_day": 1},
    )
    approval = ArtifactApproval(
        artifact_id=skeleton.artifact_id,
        artifact_revision=skeleton.revision,
        artifact_digest=skeleton.digest,
        actor_user_id="U1",
        session_revision=2,
    )
    snapshot = _locked_snapshot().model_copy(
        update={"artifacts": [skeleton], "approvals": [approval]}
    )

    report = TimeboxRequirements().evaluate(ArtifactKind.VALIDATED_CANDIDATE, snapshot)

    assert report.by_id("candidate.approved_skeleton").satisfied
    assert report.by_id("candidate.calendar_snapshot").owner is RequirementOwner.SYSTEM
    assert report.by_id("candidate.active_constraints").owner is RequirementOwner.SYSTEM
    assert (
        report.by_id("candidate.concrete_placements").owner is RequirementOwner.PLANNER
    )
    assert report.first_hard_user_blocker() is None


def test_commit_requires_an_exactly_approved_candidate() -> None:
    """Catches a regression that commits a candidate whose approval is stale or absent."""

    candidate = PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=3,
        payload={"events": []},
        dependency_revisions={"skeleton": 2},
    )
    snapshot = _locked_snapshot().model_copy(update={"artifacts": [candidate]})

    report = TimeboxRequirements().evaluate(ArtifactKind.COMMIT_RECEIPT, snapshot)

    approval = report.by_id("commit.approved_candidate")
    assert approval.owner is RequirementOwner.SYSTEM
    assert approval.resolution == "validate"
    assert not approval.satisfied


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (
            ArtifactKind.PLANNING_DAY,
            frozenset(
                {
                    ArtifactKind.DAY_FRAME,
                    ArtifactKind.CAPTURED_INPUTS,
                    ArtifactKind.PLANNING_BRIEF,
                    ArtifactKind.SKELETON,
                    ArtifactKind.VALIDATED_CANDIDATE,
                    ArtifactKind.COMMIT_RECEIPT,
                }
            ),
        ),
        (
            ArtifactKind.CAPTURED_INPUTS,
            frozenset(
                {
                    ArtifactKind.PLANNING_BRIEF,
                    ArtifactKind.SKELETON,
                    ArtifactKind.VALIDATED_CANDIDATE,
                    ArtifactKind.COMMIT_RECEIPT,
                }
            ),
        ),
        (
            ArtifactKind.SKELETON,
            frozenset(
                {
                    ArtifactKind.VALIDATED_CANDIDATE,
                    ArtifactKind.COMMIT_RECEIPT,
                }
            ),
        ),
    ],
)
def test_changed_artifact_invalidates_only_its_downstream_artifacts(
    changed: ArtifactKind, expected: frozenset[ArtifactKind]
) -> None:
    """Catches a regression that keeps descendants or discards upstream artifacts."""

    invalidated = TimeboxRequirements().invalidate_from(changed)

    assert invalidated == expected
