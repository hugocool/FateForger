from datetime import datetime, timezone

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import (
    FF_EVENT_ADD_ACTION_ID,
    FF_EVENT_BLOCK_PICK_TIME,
    FF_EVENT_EDIT_ACTION_ID,
    FF_EVENT_START_TIME_ACTION_ID,
    _card_payload,
)


def test_planning_card_includes_timepicker_and_add_button():
    """Card must show the time picker as first-class and Add to Calendar in actions."""
    draft = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.DRAFT,
        event_url=None,
        last_error=None,
    )

    payload = _card_payload(draft)
    # Timepicker is a section-level accessory
    time_section = next(
        b for b in payload["blocks"] if b.get("block_id") == FF_EVENT_BLOCK_PICK_TIME
    )
    assert time_section["accessory"]["type"] == "timepicker"
    assert time_section["accessory"]["action_id"] == FF_EVENT_START_TIME_ACTION_ID
    # Actions only has the primary button and Edit
    actions = next(block for block in payload["blocks"] if block.get("type") == "actions")
    action_ids = [el.get("action_id") for el in actions.get("elements", [])]
    assert FF_EVENT_ADD_ACTION_ID in action_ids
    assert FF_EVENT_EDIT_ACTION_ID in action_ids
