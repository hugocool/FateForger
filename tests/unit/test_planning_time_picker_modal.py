from datetime import datetime, timezone

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import _card_payload


def test_planning_card_defaults_datetimepicker_to_draft_start():
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
    actions = next(block for block in payload["blocks"] if block.get("type") == "actions")
    dt_picker = next(el for el in actions["elements"] if el.get("type") == "datetimepicker")
    assert dt_picker["initial_date_time"] == int(start.timestamp())
