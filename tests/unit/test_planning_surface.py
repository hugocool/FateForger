from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning_surface import (
    ADD_OPTION_ID,
    RETRY_OPTION_ID,
    InterpretedPlanningTurn,
    PlanningPress,
    bind,
    describe,
    planning_view,
)
from fateforger.slack_bot.surface_intents import CHOOSE_OPTION, narrow_schema


def _draft(status: DraftStatus = DraftStatus.DRAFT) -> EventDraftPayload:
    return EventDraftPayload(
        draft_id="draft_abc",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow's priorities and prep for shutdown.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 9, 3, 8, 38, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=status,
        event_url=None,
        last_error=None,
    )


def test_a_draft_offers_add_and_the_time_decisions() -> None:
    view = planning_view(_draft())

    assert view.surface_kind == "planning_card"
    assert view.display_state == "draft"
    assert [o.option_id for o in view.offered_options] == [ADD_OPTION_ID]
    assert "10:38" in view.offered_options[0].effect
    assert view.allowed_decisions == (
        "update_time",
        "update_time_and_add",
        "none",
        CHOOSE_OPTION,
    )


def test_a_failed_draft_offers_retry_instead_of_add() -> None:
    view = planning_view(_draft(DraftStatus.FAILURE))

    assert [o.option_id for o in view.offered_options] == [RETRY_OPTION_ID]


@pytest.mark.parametrize("status", [DraftStatus.PENDING, DraftStatus.SUCCESS])
def test_a_settled_draft_offers_nothing(status: DraftStatus) -> None:
    view = planning_view(_draft(status))

    assert view.offered_options == ()
    assert view.allowed_decisions == ("none",)


def test_describe_names_the_card_its_time_and_its_controls() -> None:
    text = describe(_draft())

    assert "Daily planning session" in text
    assert "10:38" in text
    assert "Add to calendar" in text
    assert "not added yet" in text.lower()


def test_bind_maps_the_add_option_to_the_add_press() -> None:
    schema = narrow_schema(InterpretedPlanningTurn, planning_view(_draft()).offered_options)
    turn = schema.model_validate({"decision": CHOOSE_OPTION, "option_id": ADD_OPTION_ID})

    assert bind(turn) == PlanningPress(kind="add", selected_time=None)


def test_bind_maps_a_time_with_consent_to_update_and_add() -> None:
    turn = InterpretedPlanningTurn(decision="update_time_and_add", selected_time="13:45")

    assert bind(turn) == PlanningPress(kind="update_time_and_add", selected_time="13:45")


def test_bind_refuses_a_time_decision_without_a_time() -> None:
    turn = InterpretedPlanningTurn(decision="update_time_and_add", selected_time=None)

    with pytest.raises(ValueError, match="without a time"):
        bind(turn)


def test_bind_none_is_no_press() -> None:
    assert bind(InterpretedPlanningTurn(decision="none")) is None
