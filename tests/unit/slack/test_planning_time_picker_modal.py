from datetime import datetime, timezone

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import (
    FF_EVENT_BLOCK_PICK_TIME,
    FF_EVENT_START_TIME_ACTION_ID,
    _card_payload,
)


def test_planning_card_timepicker_defaults_to_draft_start_time():
    """The timepicker section accessory must show the event's local start time."""
    # UTC 09:00 → Europe/Amsterdam (UTC+1 in January) = 10:00
    start = datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc)
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
        start_at_utc=start.isoformat(),
        duration_min=30,
        status=DraftStatus.DRAFT,
        event_url=None,
        last_error=None,
    )
    payload = _card_payload(draft)
    time_section = next(
        b for b in payload["blocks"] if b.get("block_id") == FF_EVENT_BLOCK_PICK_TIME
    )
    timepicker = time_section["accessory"]
    assert timepicker["type"] == "timepicker"
    assert timepicker["action_id"] == FF_EVENT_START_TIME_ACTION_ID
    # 09:00 UTC → 10:00 Europe/Amsterdam (UTC+1 winter)
    assert timepicker["initial_time"] == "10:00"
