from datetime import date

import pytest
from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactApproval,
    ArtifactKind,
    DayType,
    FactKind,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
)


def test_planning_day_derives_saturday_weekend_from_host_date() -> None:
    day = PlanningDay.lock_default(
        value=date(2026, 8, 29), timezone="Europe/Amsterdam", lock_revision=1
    )

    assert day.iso_weekday == 6
    assert day.day_type is DayType.WEEKEND


def test_artifact_digest_is_canonical() -> None:
    left = PlanningArtifact.create(
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"b": 2, "a": 1},
        dependency_revisions={"planning_day": 1},
    )
    right = PlanningArtifact.create(
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"a": 1, "b": 2},
        dependency_revisions={"planning_day": 1},
    )

    assert left.digest == right.digest


def test_snapshot_round_trip_keeps_typed_day_and_artifact() -> None:
    snapshot = PlanningSessionSnapshot.new(
        session_key="C1:1.0", owner_user_id="U1"
    ).model_copy(
        update={
            "planning_day": PlanningDay.lock_default(
                value=date(2026, 8, 29),
                timezone="Europe/Amsterdam",
                lock_revision=1,
            )
        }
    )

    assert PlanningSessionSnapshot.model_validate_json(
        snapshot.model_dump_json()
    ) == snapshot


def test_planning_day_rejects_iso_weekday_outside_calendar_range() -> None:
    with pytest.raises(ValidationError):
        PlanningDay(
            date=date(2026, 8, 29),
            timezone="Europe/Amsterdam",
            iso_weekday=8,
            day_type=DayType.WEEKEND,
            classification_basis="calendar",
            lock_revision=1,
        )


def test_artifact_approval_rejects_digest_without_sha256_shape() -> None:
    with pytest.raises(ValidationError):
        ArtifactApproval(
            artifact_id="skeleton-1",
            artifact_revision=1,
            artifact_digest="not-a-sha256-digest",
            actor_user_id="U1",
            session_revision=1,
        )


def test_snapshot_rejects_duplicate_fact_ids() -> None:
    activity = PlanningFact(
        fact_id="activity-1",
        kind=FactKind.REQUESTED_ACTIVITY,
        value="Write the proposal",
        source="user",
        source_interaction_id="1.0",
    )

    with pytest.raises(ValidationError):
        PlanningSessionSnapshot(
            session_key="C1:1.0",
            revision=1,
            owner_user_id="U1",
            facts=[activity, activity],
        )


def test_snapshot_rejects_status_outside_lifecycle_contract() -> None:
    with pytest.raises(ValidationError):
        PlanningSessionSnapshot(
            session_key="C1:1.0",
            revision=1,
            owner_user_id="U1",
            status="paused",
        )


def test_calendar_day_classification_cannot_mark_a_weekday_as_weekend() -> None:
    with pytest.raises(ValidationError):
        PlanningDay(
            date=date(2026, 8, 31),
            timezone="Europe/Amsterdam",
            iso_weekday=1,
            day_type=DayType.WEEKEND,
            classification_basis="calendar",
            lock_revision=1,
        )


def test_artifact_rejects_shaped_digest_not_matching_its_content() -> None:
    artifact = PlanningArtifact.create(
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"activities": ["Write"]},
        dependency_revisions={"planning_day": 1},
    )

    with pytest.raises(ValidationError):
        PlanningArtifact.model_validate(
            artifact.model_dump() | {"digest": "0" * 64}
        )


def test_provide_facts_rejects_duplicate_fact_ids_at_intent_boundary() -> None:
    activity = PlanningFact(
        fact_id="activity-1",
        kind=FactKind.REQUESTED_ACTIVITY,
        value="Write the proposal",
        source="user",
        source_interaction_id="1.0",
    )

    with pytest.raises(ValidationError):
        ProvidePlanningFacts(facts=[activity, activity])
