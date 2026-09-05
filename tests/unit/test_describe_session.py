"""`describe_session` says what the card says, in prose, for an agent that
cannot see the card. Fields, not sentences: the wording is free to move."""

from __future__ import annotations

from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    BlockerOption,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import (
    Asking,
    ContextItem,
    DecidedItem,
    StageCard,
    describe_session,
    stage,
)


def _planning_day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 5), timezone="Europe/Amsterdam", lock_revision=1
    )


def test_a_stage_three_card_names_its_decided_items_and_the_day() -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=5, owner_user_id="U1",
        planning_day=_planning_day(), status="open",
    )
    card = StageCard(
        stage=stage(3), session_key="C1:1.0", expected_revision=5,
        context=[ContextItem(text="Oats two hours before gym", source="memory")],
        decided=[
            DecidedItem(text="Gym at 18:00", kind="fact", ref="f1"),
            DecidedItem(text="Lunch at 13:00", kind="assumption", ref="a1", filed_by="planner"),
        ],
        body="07:00 wake · 09:00 deep work · 18:00 gym",
    )
    text = describe_session(snapshot, card)
    assert "2026-09-05" in text
    assert "Saturday" in text
    assert "3/5" in text and "Sketch" in text
    assert "Gym at 18:00" in text
    assert "Lunch at 13:00" in text and "assumption" in text and "planner" in text
    assert "Oats two hours before gym" in text
    assert "deep work" in text


def test_a_committed_session_names_the_receipt() -> None:
    # The payload keys are the ones `PendingCandidateCommitPort.commit` writes
    # (`timeboxing_host.py`): there is no calendar id and no applied count on a
    # real receipt, so the identifying fields are the transaction and what it
    # reached.
    receipt = PlanningArtifact.create(
        kind=ArtifactKind.COMMIT_RECEIPT, revision=1,
        payload={"committed": True, "tx_id": "tx_42", "reason": None,
                 "candidate_digest": "d" * 64,
                 "calendar_backend": "google", "durable": True},
        dependency_revisions={},
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=9, owner_user_id="U1",
        planning_day=_planning_day(), status="committed", artifacts=[receipt],
    )
    text = describe_session(snapshot, card=None)
    assert "committed" in text
    assert "tx_42" in text
    assert "d" * 64 in text
    assert "google" in text


def test_a_fresh_session_says_so() -> None:
    snapshot = PlanningSessionSnapshot(session_key="D1:dm", revision=0, owner_user_id="U1")
    text = describe_session(snapshot, card=None)
    assert "no planning day" in text.lower() or "not started" in text.lower()


def test_the_open_question_is_described_with_its_options_and_their_effects() -> None:
    """A user staring at two buttons and typing "what are my choices?" gets an
    answer from this text. Dropping the options leaves the answerer describing
    a question whose answers it cannot see."""

    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=5, owner_user_id="U1",
        planning_day=_planning_day(), status="open",
    )
    asking = Asking(
        requirement_id="skeleton.dinner_anchor",
        question="When is dinner?",
        why_needed="the gym block has to sit around it",
        options=[
            BlockerOption(option_id="o1", label="18:30", effect="gym moves to 16:45"),
            BlockerOption(option_id="o2", label="20:00", effect="gym stays at 18:00"),
        ],
    )
    card = StageCard(
        stage=stage(3), session_key="C1:1.0", expected_revision=5, asking=asking,
    )
    text = describe_session(snapshot, card)
    assert asking.question in text
    assert asking.why_needed in text
    for option in asking.options:
        assert option.label in text
        assert option.effect in text
