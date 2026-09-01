"""Reselecting a day must never cost the card its controls.

On 2026-08-31 at 22:57 a live `/timebox` session died at Stage 0: the user
picked "Tomorrow" in the date dropdown and the card lost every button. In the
slash-command route the date card is posted as its own session-thread root, so
`meta.thread_ts` and the card's `prompt_ts` name the same Slack message. The
reselect handler redrew the card, then "also" relabeled the thread root — a
text-only update to the very message it had just redrawn, which wiped the
blocks. The session then waited forever for a Confirm click that had no button.

Assertions are over action ids this system minted, never over card prose.
"""

from __future__ import annotations

import logging

from fateforger.slack_bot.handlers import _handle_timebox_date_reselect
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    TimeboxCommitMeta,
)

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
