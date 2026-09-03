"""One outcome, one typed card: the stage it is, what was decided, what is asked.

Every assertion is over identifiers this system minted -- stage indexes,
control kinds, fact ids, artifact ids. Nothing reads what the user wrote.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    Committed,
    FactKind,
    PendingBlocker,
    PlannerAssumption,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import (
    STAGES,
    StageCard,
    date_stage_card,
    map_outcome,
)
from fateforger.slack_bot.timebox_candidate import PendingTimeboxCandidates

from datetime import date


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
    )


def _snapshot(**update) -> PlanningSessionSnapshot:
    base = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        facts=[
            PlanningFact(
                fact_id="activity-1",
                kind=FactKind.REQUESTED_ACTIVITY,
                value="finish the memo",
                source="user",
                source_interaction_id="1.1",
            ),
            PlanningFact(
                fact_id="frame-1",
                kind=FactKind.DAY_FRAME,
                value={"wake": "07:30", "sleep": "23:00"},
                source="user",
                source_interaction_id="1.2",
            ),
        ],
        assumptions=[
            PlannerAssumption(
                assumption_id="a-1",
                requirement_id="skeleton.ordinary_placement",
                value={"gym": "17:00"},
                why_needed="gym had no time",
                invalidated_by=[],
            )
        ],
    )
    return base.model_copy(update=update)


def _planning_day_artifact() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="day-1",
        kind=ArtifactKind.PLANNING_DAY,
        revision=1,
        payload=_day().model_dump(mode="json"),
        dependency_revisions={},
    )


def _skeleton(payload: dict | None = None) -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload=payload or {"markdown": "# Morning\n- memo", "reasoning": "memo first"},
        dependency_revisions={"planning_day": 1},
    )


def _candidate() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload={
            "digest": "d" * 64,
            "snapshot": {
                "token": "tok",
                "calendar_id": "cal",
                "day": "2026-09-03",
                "tz": "Europe/Amsterdam",
                "etags": {},
                "event_ids": {},
            },
            "patch": {"ops": [{"op": "add", "start": "09:00"}]},
            "rendered": "09:00 memo",
        },
        dependency_revisions={"skeleton": 1},
    )


def _map(outcome, snapshot, pending=None) -> StageCard | None:
    return map_outcome(
        outcome,
        snapshot,
        pending=pending or PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )


def _kinds(card: StageCard) -> list[str]:
    return [control.kind for control in card.controls]


def test_the_five_stages_are_numbered_in_order() -> None:
    assert [stage.index for stage in STAGES] == [1, 2, 3, 4, 5]
    assert [stage.name for stage in STAGES] == [
        "Constraints", "Priorities", "Sketch", "Refine", "Commit",
    ]


def test_the_date_card_is_stage_one_with_a_day_type_control_and_no_back() -> None:
    card = _map(AwaitingApproval(artifact=_planning_day_artifact()), _snapshot())
    assert card is not None
    assert card.stage.index == 1
    assert _kinds(card) == ["day_type", "cancel"]
    day_type = card.controls[0]
    assert day_type.planned_date == "2026-09-03"
    assert day_type.tz_name == "Europe/Amsterdam"
    assert day_type.thread_ts == "1.0"
    assert card.expected_revision == 4


def test_a_day_frame_question_is_stage_one_and_offers_back() -> None:
    snapshot = _snapshot(
        pending_blocker=PendingBlocker(
            requirement_id="skeleton.day_frame",
            fact_kind=FactKind.DAY_FRAME,
            options=[],
        )
    )
    card = _map(
        AwaitingUser(
            requirement_id="skeleton.day_frame",
            question="When are you up?",
            why_needed="frame",
        ),
        snapshot,
    )
    assert card is not None
    assert card.stage.index == 1
    assert card.asking is not None
    assert card.asking.requirement_id == "skeleton.day_frame"
    assert _kinds(card) == ["back", "cancel"]


def test_an_activity_question_is_stage_two_showing_what_was_already_said() -> None:
    snapshot = _snapshot(
        pending_blocker=PendingBlocker(
            requirement_id="skeleton.requested_activity",
            fact_kind=FactKind.REQUESTED_ACTIVITY,
            options=[
                BlockerOption(option_id="o1", label="Memo", effect="memo first")
            ],
        )
    )
    card = _map(
        AwaitingUser(
            requirement_id="skeleton.requested_activity",
            question="What is the day for?",
            why_needed="priorities",
            options=[BlockerOption(option_id="o1", label="Memo", effect="memo first")],
        ),
        snapshot,
    )
    assert card is not None
    assert card.stage.index == 2
    assert [item.ref for item in card.decided if item.kind == "fact"] == ["activity-1"]
    assert card.asking is not None
    assert [option.option_id for option in card.asking.options] == ["o1"]


def test_a_question_with_no_pending_blocker_defaults_to_stage_two() -> None:
    card = _map(
        AwaitingUser(requirement_id="x", question="?", why_needed="y"),
        _snapshot(pending_blocker=None),
    )
    assert card is not None and card.stage.index == 2


def test_the_skeleton_is_stage_three_with_approve_back_cancel() -> None:
    card = _map(AwaitingApproval(artifact=_skeleton()), _snapshot())
    assert card is not None
    assert card.stage.index == 3
    assert _kinds(card) == ["approve", "back", "cancel"]
    approve = card.controls[0]
    assert approve.artifact_id == "skeleton-1"
    assert approve.artifact_digest == _skeleton().digest
    assert card.body == "# Morning\n- memo"
    assert [item.ref for item in card.decided if item.kind == "assumption"] == ["a-1"]


def test_a_skeleton_without_markdown_fails_loudly() -> None:
    with pytest.raises(ValidationError):
        _map(AwaitingApproval(artifact=_skeleton({"blocks": []})), _snapshot())


def test_the_candidate_is_stage_four_and_arms_the_commit_gate() -> None:
    pending = PendingTimeboxCandidates()
    card = _map(AwaitingApproval(artifact=_candidate()), _snapshot(), pending)
    assert card is not None
    assert card.stage.index == 4
    assert _kinds(card) == ["commit", "back", "cancel"]
    commit = card.controls[0]
    assert commit.calendar_id == "cal" and commit.day == "2026-09-03"
    # The gate spends the same id the card offered.
    assert pending.peek("C1:1.0") is not None
    assert pending.peek("C1:1.0").candidate_id == commit.candidate_id
    assert "09:00 memo" in card.body


def test_a_commit_is_stage_five_with_undo_only() -> None:
    receipt = PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"committed": True, "tx_id": "tx-9", "durable": True},
        dependency_revisions={"validated_candidate": 1},
    )
    card = _map(Committed(receipt=receipt), _snapshot(status="committed"))
    assert card is not None
    assert card.stage.index == 5
    assert _kinds(card) == ["undo"]
    assert card.controls[0].tx_id == "tx-9"


def test_a_refused_commit_is_stage_five_without_undo() -> None:
    receipt = PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"committed": False, "reason": "etag_mismatch"},
        dependency_revisions={"validated_candidate": 1},
    )
    card = _map(Committed(receipt=receipt), _snapshot())
    assert card is not None
    assert card.stage.index == 5
    assert _kinds(card) == []


def test_a_receipt_keeps_the_stage_and_drops_every_control() -> None:
    card = _map(AwaitingApproval(artifact=_skeleton()), _snapshot())
    receipt = card.as_receipt("✅ confirmed")
    assert receipt.stage == card.stage
    assert receipt.controls == [] and receipt.asking is None
    assert receipt.done == "✅ confirmed"
    assert receipt.body == card.body


def test_date_stage_card_matches_the_mapped_date_card() -> None:
    direct = date_stage_card(
        session_key="C1:1.0",
        expected_revision=4,
        user_id="U1",
        channel_id="C1",
        thread_ts="1.0",
        planned_date="2026-09-03",
        tz_name="Europe/Amsterdam",
    )
    mapped = _map(AwaitingApproval(artifact=_planning_day_artifact()), _snapshot())
    assert direct == mapped


def test_stage_cards_knows_no_slack() -> None:
    """The mapper is the one place a card's content is decided, and it stays
    testable without a client: no slack_sdk, and none of the modules that
    render or route (an import from either would drag a client in)."""
    import ast
    import inspect

    import fateforger.slack_bot.stage_cards as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"slack_sdk", "handlers", "timeboxing_cards", "timeboxing_commit"}
    offending = {
        name for name in imported if any(part in forbidden for part in name.split("."))
    }
    assert offending == set(), offending
