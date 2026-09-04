from datetime import date

import pytest
from pydantic import TypeAdapter, ValidationError

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactApproval,
    ArtifactKind,
    BlockerOption,
    ChooseBlockerOption,
    DayType,
    FactKind,
    PendingBlocker,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    TimeboxIntent,
    UserBlockerDraft,
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


def test_blocker_options_stop_at_four() -> None:
    """A closed answer set the user has to read is not a menu.

    Four is the point past which a button row stops being a shortcut and starts
    being a form, and a planner that found five materially different answers has
    found an open question, which is the case buttons must not be forced onto.
    """

    with pytest.raises(ValidationError):
        UserBlockerDraft(
            requirement_id="skeleton.day_shape",
            why_needed="the afternoon has several equally workable shapes",
            options=[
                BlockerOption(
                    option_id=f"option-{index}",
                    label=f"Shape {index}",
                    effect="rearranges the afternoon",
                )
                for index in range(1, 6)
            ],
        )


def test_a_blocker_with_no_options_is_a_complete_blocker() -> None:
    """Most questions have no closed answer set, and inventing one loses answers.

    "What do you want to get out of the day?" has as many answers as Hugo has
    days. Requiring options here would push a planner into offering four guesses
    and hiding the fifth answer, which is the failure buttons exist to avoid.
    """

    draft = UserBlockerDraft(
        requirement_id="skeleton.requested_activity",
        why_needed="a skeleton needs at least one intended activity",
    )

    assert draft.options == []


def test_two_options_cannot_share_one_identifier() -> None:
    """Catches an ambiguous press: two buttons, one answer, no way to tell which."""

    with pytest.raises(ValidationError):
        UserBlockerDraft(
            requirement_id="skeleton.day_shape",
            why_needed="the afternoon has two equally workable shapes",
            options=[
                BlockerOption(
                    option_id="option-1",
                    label="Deep work first",
                    effect="moves the gym after dinner",
                ),
                BlockerOption(
                    option_id="option-1",
                    label="Gym first",
                    effect="moves deep work to the evening",
                ),
            ],
        )


def test_choosing_an_option_is_a_discriminated_planning_intent() -> None:
    """A press must survive the same typed transport every other intent uses."""

    adapter = TypeAdapter(TimeboxIntent)

    intent = adapter.validate_python(
        {
            "kind": "choose_blocker_option",
            "requirement_id": "skeleton.day_shape",
            "option_id": "option-2",
        }
    )

    assert isinstance(intent, ChooseBlockerOption)
    assert intent.option_id == "option-2"


def test_snapshot_round_trip_keeps_the_question_it_is_still_holding() -> None:
    """The press lands a turn later, so what was offered has to be durable.

    Recomputing the option set at press time would let a changed planner offer a
    different set than the user is looking at, and the press would then answer a
    question nobody asked.
    """

    snapshot = PlanningSessionSnapshot.new(
        session_key="C1:1.0", owner_user_id="U1"
    ).model_copy(
        update={
            "pending_blocker": PendingBlocker(
                requirement_id="skeleton.day_shape",
                fact_kind=FactKind.ORDINARY_PLACEMENT,
                options=[
                    BlockerOption(
                        option_id="option-1",
                        label="Deep work first",
                        effect="moves the gym after dinner",
                    )
                ],
            )
        }
    )

    restored = PlanningSessionSnapshot.model_validate_json(snapshot.model_dump_json())

    assert restored == snapshot


from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    CellRef,
    DenyAssumption,
    FileAssumption,
    Gate,
    GateMet,
    PlannerAssumption,
    RestoreConstraint,
    coverage_fact_id,
    elicited_fact_id,
    suspension_fact_id,
)


def test_stable_fact_ids_are_minted_from_identifiers_only() -> None:
    assert coverage_fact_id(date(2026, 9, 8)) == "coverage:2026-09-08"
    assert suspension_fact_id("abc123") == "suspend:abc123"
    first, second = elicited_fact_id("elicit.body.unclear"), elicited_fact_id("elicit.body.unclear")
    assert first.startswith("elicited:elicit.body.unclear:")
    assert first != second
    assert elicited_fact_id(None).startswith("elicited:free:")


def test_a_cell_ref_names_its_requirement() -> None:
    assert CellRef(row="body", criterion="unclear").id == "elicit.body.unclear"


def test_gate_met_carries_no_open_cells() -> None:
    gate = Gate(open_cells=[], day_label="working Tuesday")
    assert GateMet(gate=gate).kind == "gate_met"
    assert gate.note is None


def test_an_assumption_is_filed_by_the_planner_unless_said_otherwise() -> None:
    base = dict(assumption_id="a1", requirement_id="elicit.body.unclear", value="x", why_needed="y")
    assert PlannerAssumption(**base).filed_by == "planner"
    assert PlannerAssumption(**base, filed_by="user").filed_by == "user"


def test_new_intents_are_discriminated_by_kind() -> None:
    assert DenyAssumption(assumption_id="a1").kind == "deny_assumption"
    assert FileAssumption(requirement_id="r", value="v", why_needed="w").kind == "file_assumption"
    assert RestoreConstraint(constraint_uid="c1").kind == "restore_constraint"


def test_a_snapshot_without_the_new_fields_still_loads() -> None:
    """Stored sessions predate these fields; the defaults must carry them."""
    from fateforger.agents.timeboxing.session_contracts import PlanningSessionSnapshot

    snapshot = PlanningSessionSnapshot.model_validate(
        {"session_key": "C1:1.0", "revision": 0, "owner_user_id": "U1"}
    )
    assert snapshot.stage1 == "open"
    assert snapshot.applicable_constraints == []
    assert snapshot.suspended_constraint_count == 0
