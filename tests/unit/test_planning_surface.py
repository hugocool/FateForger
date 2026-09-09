from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning_surface import (
    ADD_OPTION_ID,
    RETRY_OPTION_ID,
    InterpretedPlanningTurn,
    InterpretedSettledPlanningTurn,
    PlanningPress,
    bind,
    describe,
    planning_view,
    schema_for,
)
from fateforger.slack_bot.surface_intents import CHOOSE_OPTION, narrow_schema


#: The card's own day, an hour and a half before the slot it proposes. Fixed,
#: because a view that read the clock would make these assertions depend on
#: the weekday the suite happens to run on.
_NOW = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)


def _draft(
    status: DraftStatus = DraftStatus.DRAFT, event_url: str | None = None
) -> EventDraftPayload:
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
        event_url=event_url,
        last_error=None,
    )


def test_a_draft_offers_add_and_the_time_decisions() -> None:
    view = planning_view(_draft(), now=_NOW)

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
    view = planning_view(_draft(DraftStatus.FAILURE), now=_NOW)

    assert [o.option_id for o in view.offered_options] == [RETRY_OPTION_ID]


@pytest.mark.parametrize("status", [DraftStatus.PENDING, DraftStatus.SUCCESS])
def test_a_settled_draft_offers_nothing(status: DraftStatus) -> None:
    view = planning_view(_draft(status), now=_NOW)

    assert view.offered_options == ()
    assert view.allowed_decisions == ("none",)


def test_describe_names_the_card_its_time_and_its_controls() -> None:
    text = describe(_draft())

    assert "Daily planning session" in text
    assert "10:38" in text
    assert "Add to calendar" in text
    assert "not added yet" in text.lower()


def test_bind_maps_the_add_option_to_the_add_press() -> None:
    schema = narrow_schema(InterpretedPlanningTurn, planning_view(_draft(), now=_NOW).offered_options)
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


def test_bind_maps_the_retry_option_to_the_retry_press() -> None:
    schema = narrow_schema(InterpretedPlanningTurn, planning_view(_draft(DraftStatus.FAILURE), now=_NOW).offered_options)
    turn = schema.model_validate({"decision": CHOOSE_OPTION, "option_id": RETRY_OPTION_ID})

    assert bind(turn) == PlanningPress(kind="retry", selected_time=None)


def test_bind_raises_for_choose_option_with_unoffered_option_id() -> None:
    schema = narrow_schema(InterpretedPlanningTurn, planning_view(_draft(), now=_NOW).offered_options)
    # Use model_construct to bypass validation, since the schema rejects it
    turn = schema.model_construct(decision=CHOOSE_OPTION, option_id="cancel")

    with pytest.raises(ValueError, match="without an offered option"):
        bind(turn)


def test_a_failed_draft_offers_the_full_decision_set() -> None:
    view = planning_view(_draft(DraftStatus.FAILURE), now=_NOW)

    assert view.allowed_decisions == (
        "update_time",
        "update_time_and_add",
        "none",
        CHOOSE_OPTION,
    )


@pytest.mark.parametrize("status", [DraftStatus.PENDING, DraftStatus.SUCCESS])
def test_a_settled_cards_schema_cannot_express_a_time_decision(status: DraftStatus) -> None:
    # The view allows only `none` there; leaving the time decisions in the
    # schema let the model answer one and turned a routable question into
    # "I couldn't read that reply".
    schema = schema_for(_draft(status))

    assert schema is InterpretedSettledPlanningTurn
    with pytest.raises(ValidationError):
        schema.model_validate({"decision": "update_time", "selected_time": "17:00"})
    assert bind(schema.model_validate({"decision": "none"})) is None


@pytest.mark.parametrize("status", [DraftStatus.DRAFT, DraftStatus.FAILURE])
def test_a_live_card_keeps_the_full_turn_schema(status: DraftStatus) -> None:
    assert schema_for(_draft(status)) is InterpretedPlanningTurn


def test_an_added_card_describes_its_calendar_link() -> None:
    # The routed agent is the one asked "where did it go?"; without the link
    # it has to answer that it cannot say.
    text = describe(_draft(DraftStatus.SUCCESS, event_url="https://cal.example/e/1"))

    assert "Calendar link: https://cal.example/e/1" in text


def test_a_draft_has_no_calendar_link_to_describe() -> None:
    assert "Calendar link" not in describe(_draft())


def test_the_view_carries_the_time_it_was_given_in_the_drafts_timezone() -> None:
    # 23:30 UTC on 2 Sep is already 3 Sep in Amsterdam. The block names the
    # user's day, not UTC's -- and it names the instant passed in, never one
    # the view read from the clock, which is what keeps the eval's answer the
    # same on a Wednesday and on a Thursday.
    view = planning_view(_draft(), now=datetime(2026, 9, 2, 23, 30, tzinfo=timezone.utc))

    assert view.context["now"] == {
        "date": "2026-09-03",
        "weekday": "Thursday",
        "time": "01:30",
    }


def test_the_view_will_not_read_the_clock_for_itself() -> None:
    # No default: a caller that forgets `now` must fail here, loudly, rather
    # than silently getting whatever day the process happens to be running on.
    with pytest.raises(TypeError):
        planning_view(_draft())  # type: ignore[call-arg]


def test_the_view_refuses_a_now_that_names_no_timezone() -> None:
    # The no-default rule stops a view from inventing the instant; this stops
    # it from inventing the offset. A naive datetime reads as the host's local
    # time, so the same call would answer differently on a machine in another
    # zone -- the host-dependent answer, one layer down.
    with pytest.raises(ValueError, match="timezone"):
        planning_view(_draft(), now=datetime(2026, 9, 3, 7, 0))
