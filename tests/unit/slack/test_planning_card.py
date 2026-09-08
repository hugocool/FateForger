"""The planning card: what it renders, the controls it carries, and what a
press does to it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.slack_bot.planning import (
    FF_EVENT_ADD_ACTION_ID,
    FF_EVENT_ADD_DISABLED_ACTION_ID,
    FF_EVENT_BLOCK_PICK_TIME,
    FF_EVENT_EDIT_ACTION_ID,
    FF_EVENT_OPEN_URL_ACTION_ID,
    FF_EVENT_RETRY_ACTION_ID,
    FF_EVENT_START_TIME_ACTION_ID,
    _card_payload,
    parse_draft_id_from_value,
)
from fateforger.slack_bot.planning import (
    FF_EVENT_ADD_ACTION_ID,
    FF_EVENT_BLOCK_PICK_TIME,
    FF_EVENT_EDIT_ACTION_ID,
    FF_EVENT_START_TIME_ACTION_ID,
    _card_payload,
)
from fateforger.slack_bot.planning import (
    FF_EVENT_BLOCK_PICK_TIME,
    FF_EVENT_START_TIME_ACTION_ID,
    _card_payload,
)
import logging
from fateforger.slack_bot.handlers import _handle_timebox_date_reselect
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    TimeboxCommitMeta,
)
import asyncio
from types import SimpleNamespace
import pytest
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.planning import PlanningCoordinator


# ── the card itself ───────────────────────────────────────────────────────────

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
    payload = _card_payload(
        _draft(
            status=DraftStatus.SUCCESS,
            event_url=(
                "https://www.google.com/calendar/event?eid="
                "ZmZwbGFubmluZ3h5eiBodWdvLmV2ZXJzQGV4YW1wbGUuY29t"
            ),
        )
    )
    blocks = payload["blocks"]

    # No timepicker section on a committed card
    assert not any(b.get("block_id") == FF_EVENT_BLOCK_PICK_TIME for b in blocks)

    actions = next(b for b in blocks if b["type"] == "actions")
    button = actions["elements"][0]
    assert button.get("action_id") == FF_EVENT_OPEN_URL_ACTION_ID
    assert button.get("url", "").startswith("https://www.google.com/calendar/event")
    # No Edit button on a committed card
    assert not any(e.get("action_id") == FF_EVENT_EDIT_ACTION_ID for e in actions["elements"])

    status_context = next(
        b for b in payload["blocks"] if b.get("block_id") == "status"
    )
    text = status_context["elements"][0]["text"]
    assert "Open in Google Calendar" in text
    assert "https://www.google.com/calendar/event" in text


# ── the dismiss control ───────────────────────────────────────────────────────

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


# ── the time picker modal ─────────────────────────────────────────────────────

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


# ── re-selecting the date keeps the card ──────────────────────────────────────

logger = logging.getLogger(__name__)


class _RecordingClient:
    """Records chat_update calls in order; that order is what the bug lives in."""

    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def chat_update(self, **kwargs) -> dict:
        self.updates.append(kwargs)
        return {"ok": True}


def _meta(*, thread_ts: str) -> TimeboxCommitMeta:
    return TimeboxCommitMeta(
        session_key="C0AA6HC1RJL:1788209836.705989",
        expected_revision=1,
        user_id="U095637NL8P",
        channel_id="C0AA6HC1RJL",
        thread_ts=thread_ts,
        date="2026-08-31",
        tz="Europe/Amsterdam",
    )


def _action_ids(blocks: list[dict]) -> set[str]:
    return {
        element["action_id"]
        for block in blocks
        if block.get("type") == "actions"
        for element in block.get("elements", [])
        if "action_id" in element
    }


async def test_reselect_on_a_card_that_is_its_own_thread_root_keeps_confirm():
    """The last write to the card message must still carry the Confirm control."""
    card_ts = "1788209836.705989"
    client = _RecordingClient()

    await _handle_timebox_date_reselect(
        client=client,
        logger=logger,
        value=_meta(thread_ts=card_ts).to_value(),
        selected_date="2026-09-01",
        prompt_channel_id="C0AA6HC1RJL",
        prompt_ts=card_ts,
    )

    writes_to_card = [u for u in client.updates if u.get("ts") == card_ts]
    assert writes_to_card, "the card was never redrawn"
    final = writes_to_card[-1]
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(
        final.get("blocks") or []
    ), "the last write to the card message erased its controls"


async def test_reselect_with_a_separate_thread_root_still_relabels_it():
    """When root and card are distinct messages, the root label must keep moving."""
    root_ts = "1788197342.182029"
    card_ts = "1788197347.548439"
    client = _RecordingClient()

    await _handle_timebox_date_reselect(
        client=client,
        logger=logger,
        value=_meta(thread_ts=root_ts).to_value(),
        selected_date="2026-09-01",
        prompt_channel_id="C0AA6HC1RJL",
        prompt_ts=card_ts,
    )

    writes_to_card = [u for u in client.updates if u.get("ts") == card_ts]
    writes_to_root = [u for u in client.updates if u.get("ts") == root_ts]
    assert FF_TIMEBOX_COMMIT_START_ACTION_ID in _action_ids(
        (writes_to_card[-1] if writes_to_card else {}).get("blocks") or []
    )
    assert writes_to_root, "the separate thread root lost its relabel"


# ── instrumentation on register ───────────────────────────────────────────────

class _SlowGuardian:
    async def reconcile_user(self, *, user_id: str) -> None:
        await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_maybe_register_user_records_cancelled_reconcile(monkeypatch):
    stage_calls: list[str] = []
    error_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "fateforger.slack_bot.planning.observe_stage_duration",
        lambda *, stage, duration_s: stage_calls.append(stage),
    )
    monkeypatch.setattr(
        "fateforger.slack_bot.planning.record_error",
        lambda *, component, error_type: error_calls.append((component, error_type)),
    )

    runtime = SimpleNamespace(planning_guardian=_SlowGuardian())
    focus = FocusManager(ttl_seconds=3600, allowed_agents=["receptionist_agent"])
    coordinator = PlanningCoordinator(runtime=runtime, focus=focus, client=SimpleNamespace())

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            coordinator.maybe_register_user(
                user_id="U1",
                channel_id="D1",
                channel_type="im",
            ),
            timeout=0.01,
        )

    assert "planning_register_ensure_anchor" in stage_calls
    assert "planning_guardian_reconcile_cancelled" in stage_calls
    assert ("planning_guardian", "reconcile_cancelled") in error_calls
