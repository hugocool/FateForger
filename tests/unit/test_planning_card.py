from datetime import datetime, timezone

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import (
    FF_EVENT_ADD_ACTION_ID,
    FF_EVENT_ADD_DISABLED_ACTION_ID,
    FF_EVENT_BLOCK_PICK_TIME,
    FF_EVENT_EDIT_ACTION_ID,
    FF_EVENT_RETRY_ACTION_ID,
    FF_EVENT_START_TIME_ACTION_ID,
    _card_payload,
    parse_draft_id_from_value,
)


def _draft(**overrides):
    base = EventDraftPayload(
        draft_id="draft_abc123",
        user_id="U1",
        channel_id="D1",
        message_ts="123.456",
        calendar_id="primary",
        event_id="ffplanningxyz",
        title="Daily planning session",
        description="Plan tomorrow.",
        timezone="Europe/Amsterdam",
        start_at_utc=datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc).isoformat(),
        duration_min=30,
        status=DraftStatus.DRAFT,
        event_url=None,
        last_error=None,
    )
    return base.__class__(**{**base.__dict__, **overrides})


def test_parse_draft_id():
    assert parse_draft_id_from_value('{"draft_id":"draft_abc"}') == "draft_abc"
    assert parse_draft_id_from_value("") is None


def test_card_initial_has_timepicker_and_add():
    """Time is first-class: a timepicker section appears; duration is NOT in the card actions."""
    payload = _card_payload(_draft())
    blocks = payload["blocks"]

    # Timepicker lives in a section block, not the actions row
    time_section = next(b for b in blocks if b.get("block_id") == FF_EVENT_BLOCK_PICK_TIME)
    assert time_section["type"] == "section"
    accessory = time_section["accessory"]
    assert accessory["type"] == "timepicker"
    assert accessory["action_id"] == FF_EVENT_START_TIME_ACTION_ID
    assert accessory["initial_time"] == "10:00"  # UTC 09:00 → Europe/Amsterdam 10:00

    # Actions row has Add + Edit — no duration dropdown
    actions = next(b for b in blocks if b["type"] == "actions")
    action_ids = [e["action_id"] for e in actions["elements"]]
    assert FF_EVENT_ADD_ACTION_ID in action_ids
    assert FF_EVENT_EDIT_ACTION_ID in action_ids
    # Duration must NOT be in the card actions (it lives in the Edit modal)
    assert not any("duration" in aid for aid in action_ids)


def test_card_pending_swaps_button():
    payload = _card_payload(_draft(status=DraftStatus.PENDING))
    actions = next(b for b in payload["blocks"] if b["type"] == "actions")
    action_ids = [e["action_id"] for e in actions["elements"]]
    assert FF_EVENT_ADD_DISABLED_ACTION_ID in action_ids


def test_card_failure_has_retry():
    payload = _card_payload(_draft(status=DraftStatus.FAILURE, last_error="oops"))
    actions = next(b for b in payload["blocks"] if b["type"] == "actions")
    action_ids = [e["action_id"] for e in actions["elements"]]
    assert FF_EVENT_RETRY_ACTION_ID in action_ids


def test_card_success_has_open_url_no_timepicker():
    """After booking the event, the timepicker is hidden and no Edit button is shown."""
    payload = _card_payload(_draft(status=DraftStatus.SUCCESS, event_url="https://example.com"))
    blocks = payload["blocks"]

    # No timepicker section on a committed card
    assert not any(b.get("block_id") == FF_EVENT_BLOCK_PICK_TIME for b in blocks)

    actions = next(b for b in blocks if b["type"] == "actions")
    button = actions["elements"][0]
    assert button.get("url") == "https://example.com"
    # No Edit button on a committed card
    assert not any(e.get("action_id") == FF_EVENT_EDIT_ACTION_ID for e in actions["elements"])

    status_context = next(
        b for b in payload["blocks"] if b.get("block_id") == "status"
    )
    text = status_context["elements"][0]["text"]
    assert "Open in Google Calendar" in text
    assert "https://example.com" in text
