from datetime import date

import pytest

from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CoverageMatrix
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
    coverage_fact_id,
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
        _fact(
            fact_id="frame-1",
            kind=FactKind.DAY_FRAME,
            value={"wake": "08:00", "sleep": "23:30"},
        ),
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


def test_a_day_whose_sleep_window_nobody_stated_is_a_user_blocker() -> None:
    """The 2026-09-02 session assumed the frame and the user had to correct it after commit.

    Wake and sleep are the user's to state. With an activity given and no frame
    fact, the frame is the one hard user-owned gap, so the ladder must stop and
    ask rather than let the planner file an assumption in its place.
    """

    snapshot = _locked_snapshot(
        _fact(
            fact_id="activity-1",
            kind=FactKind.REQUESTED_ACTIVITY,
            value="serious c2f work, gym for chest",
        ),
    )

    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, snapshot)

    blocker = report.first_hard_user_blocker()
    assert blocker is not None
    assert blocker.requirement_id == "skeleton.day_frame"
    assert blocker.owner is RequirementOwner.USER
    assert blocker.resolution == "ask"


def test_a_stated_sleep_window_satisfies_the_frame_whoever_stated_it() -> None:
    """A frame from the constraint corpus counts the same as one typed today."""

    snapshot = _locked_snapshot(
        _fact(
            fact_id="activity-1",
            kind=FactKind.REQUESTED_ACTIVITY,
            value="serious c2f work",
        ),
        PlanningFact(
            fact_id="frame:2026-08-29",
            kind=FactKind.DAY_FRAME,
            value={"wake": "08:30", "sleep": "00:30"},
            source="constraint_memory",
        ),
    )

    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, snapshot)

    assert report.by_id("skeleton.day_frame").satisfied
    assert report.first_hard_user_blocker() is None


def test_the_activity_is_asked_before_the_frame() -> None:
    """With nothing stated, the first question is what the day is for."""

    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, _locked_snapshot())

    blocker = report.first_hard_user_blocker()
    assert blocker is not None
    assert blocker.requirement_id == "skeleton.requested_activity"


def test_an_unreadable_activity_name_is_the_planners_to_raise_and_the_users_to_settle() -> None:
    """`Validate agent-in-ysis demos` went onto the calendar verbatim on 2026-09-02.

    Which names are unreadable is a model judgement, so the catalog cannot
    compute it; what the catalog can do is hold a user-owned requirement open
    that the planner may raise a blocker against, with its proposed readings as
    the options. It is soft: a readable name needs no answer and must not stop
    the ladder.
    """

    snapshot = _locked_snapshot(
        _fact(
            fact_id="activity-1",
            kind=FactKind.REQUESTED_ACTIVITY,
            value="validate the agent-in-ysis demos",
        ),
        _fact(
            fact_id="frame-1",
            kind=FactKind.DAY_FRAME,
            value={"wake": "08:30", "sleep": "00:30"},
        ),
    )

    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, snapshot)

    reading = report.by_id("skeleton.activity_reading")
    assert reading.owner is RequirementOwner.USER
    assert not reading.hard
    assert not reading.satisfied
    assert reading.resolution == "ask"
    assert report.first_hard_user_blocker() is None


def test_a_chosen_reading_settles_the_name() -> None:
    snapshot = _locked_snapshot(
        _fact(fact_id="activity-1", kind=FactKind.REQUESTED_ACTIVITY, value="x"),
        _fact(
            fact_id="reading-1",
            kind=FactKind.ACTIVITY_READING,
            value={
                "requirement_id": "skeleton.activity_reading",
                "label": "Validate the agent analysis demos",
                "effect": "the block is titled that way",
            },
        ),
    )

    report = TimeboxRequirements().evaluate(ArtifactKind.SKELETON, snapshot)

    assert report.by_id("skeleton.activity_reading").satisfied


def test_every_requirement_carries_a_stage_and_the_ladder_is_monotone() -> None:
    reqs = TimeboxRequirements()
    assert reqs.stage_of("skeleton.locked_day") == 1
    assert reqs.stage_of("skeleton.day_frame") == 1
    assert reqs.stage_of("skeleton.requested_activity") == 2
    assert reqs.stage_of("skeleton.activity_reading") == 2
    assert reqs.stage_of("candidate.calendar_snapshot") == 4
    assert reqs.stage_of("commit.approved_candidate") == 5


def test_forty_cells_are_soft_user_owned_stage_one_requirements() -> None:
    reqs = TimeboxRequirements()
    report = reqs.evaluate(ArtifactKind.SKELETON, _locked_snapshot())
    cells = [gap for gap in report.gaps if gap.requirement.cell is not None]
    assert len(cells) == 40
    assert all(gap.owner is RequirementOwner.USER and not gap.hard for gap in cells)
    assert all(reqs.stage_of(gap.requirement_id) == 1 for gap in cells)
    assert report.first_hard_user_blocker() is not None  # still day_frame / activity


def test_a_cell_is_satisfied_unless_the_matrix_says_uncovered() -> None:
    reqs = TimeboxRequirements()
    cell = ALL_CELLS[0]
    matrix = CoverageMatrix(cells={c.id: "not_applicable" for c in ALL_CELLS} | {cell.id: "uncovered"})
    snapshot = _locked_snapshot(
        PlanningFact(
            fact_id=coverage_fact_id(date(2026, 8, 29)),
            kind=FactKind.COVERAGE_MATRIX,
            value=matrix.model_dump(mode="json"),
            source="system",
        )
    )
    report = reqs.evaluate(ArtifactKind.SKELETON, snapshot)
    assert report.by_id(cell.id).satisfied is False
    assert report.by_id(ALL_CELLS[1].id).satisfied is True
    assert reqs.evaluate(ArtifactKind.SKELETON, _locked_snapshot()).by_id(cell.id).satisfied is True


def test_an_unknown_requirement_has_no_stage() -> None:
    import pytest

    with pytest.raises(KeyError):
        TimeboxRequirements().stage_of("nothing.like.this")
