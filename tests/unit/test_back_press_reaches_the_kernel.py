"""The Back button's value, as the renderer encodes it, decodes to `GoBack`
and is handed to the turn as the typed intent -- the press is not a string
the handler interprets."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    FactKind,
    GoBack,
    PendingBlocker,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import map_outcome
from fateforger.slack_bot.timebox_candidate import PendingTimeboxCandidates
from fateforger.slack_bot.timeboxing_cards import (
    FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
    FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
    render_stage_card,
)
from fateforger.slack_bot.timeboxing_intents import intent_from_artifact_action


def _back_button_value() -> str:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
        ),
    )
    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "# Morning", "reasoning": ""},
        dependency_revisions={"planning_day": 1},
    )
    card = map_outcome(
        AwaitingApproval(artifact=skeleton),
        snapshot,
        pending=PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )
    rendered = render_stage_card(card)
    for block in rendered.blocks:
        if block.get("type") != "actions":
            continue
        for element in block["elements"]:
            if element.get("action_id") == FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID:
                return element["value"]
    raise AssertionError("the skeleton card has no Back button")


@pytest.mark.asyncio
async def test_a_back_press_is_delivered_as_a_go_back_intent(monkeypatch) -> None:
    delivered: list[dict] = []

    async def capture(**kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr(handlers, "_deliver_timebox_turn", capture)

    await handlers._handle_timebox_artifact_action(
        runtime=object(),
        client=object(),
        logger=logging.getLogger(__name__),
        value=_back_button_value(),
        channel_id="C1",
        thread_ts="1.0",
        actor_user_id="U1",
        interaction_id="press-1",
    )

    assert len(delivered) == 1
    envelope = delivered[0]["action"]
    assert isinstance(envelope.intent, GoBack)
    assert envelope.session_key == "C1:1.0"
    assert envelope.expected_revision == 3


def _every_artifact_card() -> list:
    """Every card the mapper draws artifact controls on: a question with
    options, the skeleton, the candidate. The date card's picker and the
    commit gate decode through their own metadata and are covered by
    `test_render_stage_card.py`."""
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
        ),
        pending_blocker=PendingBlocker(
            requirement_id="skeleton.requested_activity",
            fact_kind=FactKind.REQUESTED_ACTIVITY,
            options=[BlockerOption(option_id="o1", label="Memo", effect="memo first")],
        ),
    )
    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "# Morning", "reasoning": ""},
        dependency_revisions={"planning_day": 1},
    )
    candidate = PlanningArtifact.create(
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
            "patch": {"ops": []},
            "rendered": "09:00 memo",
        },
        dependency_revisions={"skeleton": 1},
    )
    outcomes = [
        AwaitingUser(
            requirement_id="skeleton.requested_activity",
            question="What is the day for?",
            why_needed="priorities",
            options=[BlockerOption(option_id="o1", label="Memo", effect="memo first")],
        ),
        AwaitingApproval(artifact=skeleton),
        AwaitingApproval(artifact=candidate),
    ]
    cards = []
    for outcome in outcomes:
        card = map_outcome(
            outcome,
            snapshot,
            pending=PendingTimeboxCandidates(),
            actor_user_id="U1",
            session_key="C1:1.0",
            channel_id="C1",
            thread_ts="1.0",
        )
        assert card is not None
        cards.append(card)
    return cards


def test_every_drawn_artifact_control_decodes_to_an_intent_at_this_revision() -> None:
    """The control table is the only reader of a button's value. A button
    the renderer draws that the table cannot read is a live-looking control
    that answers nothing -- the shape #265's thread had."""
    artifact_control_ids = {
        FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
        FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
        FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
        FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
    }
    seen: set[str] = set()
    for card in _every_artifact_card():
        for block in render_stage_card(card).blocks:
            if block.get("type") != "actions":
                continue
            for element in block["elements"]:
                if element.get("action_id") not in artifact_control_ids:
                    continue
                seen.add(element["action_id"])
                envelope = intent_from_artifact_action(element["value"])
                assert envelope is not None, element
                assert envelope.session_key == "C1:1.0"
                assert envelope.expected_revision == 3
    assert seen == artifact_control_ids
