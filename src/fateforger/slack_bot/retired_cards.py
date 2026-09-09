"""One answer for every button the legacy timeboxing agent left in Slack.

The seven action ids below were posted by that agent's stage and submit
cards. The agent is gone; a press must say so, not fail. The ids are
kept verbatim because Slack will keep sending them for as long as the
messages exist.
"""

from __future__ import annotations

from typing import Any

FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID = "ff_timebox_confirm_submit"
FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID = "ff_timebox_cancel_submit"
FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID = "ff_timebox_undo_submit"
FF_TIMEBOX_STAGE_PROCEED_ACTION_ID = "ff_timebox_stage_proceed"
FF_TIMEBOX_STAGE_BACK_ACTION_ID = "ff_timebox_stage_back"
FF_TIMEBOX_STAGE_REDO_ACTION_ID = "ff_timebox_stage_redo"
FF_TIMEBOX_STAGE_CANCEL_ACTION_ID = "ff_timebox_stage_cancel"

RETIRED_ACTION_IDS: tuple[str, ...] = (
    FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID,
    FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID,
    FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID,
    FF_TIMEBOX_STAGE_PROCEED_ACTION_ID,
    FF_TIMEBOX_STAGE_BACK_ACTION_ID,
    FF_TIMEBOX_STAGE_REDO_ACTION_ID,
    FF_TIMEBOX_STAGE_CANCEL_ACTION_ID,
)

RETIRED_CARD_TEXT = (
    "This card is from a retired planning flow and its buttons no longer do "
    "anything. Start again with /timebox."
)


def _text_section_block(*, text: str) -> dict[str, Any]:
    """Render markdown text content as a Slack section block.

    A private copy rather than an import from `slack_bot.messages`: this
    module is the one thing here that does not die with the legacy agent, so
    it does not share plumbing with the modules that do.
    """
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text or "(no response)"},
    }


async def retire_card(*, client, body: dict) -> None:
    """Rewrite the pressed message in place; a press with no message is a no-op."""
    channel_id = (body.get("channel") or {}).get("id") or ""
    message_ts = (body.get("message") or {}).get("ts") or ""
    if not (channel_id and message_ts):
        return
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=RETIRED_CARD_TEXT,
        blocks=[_text_section_block(text=RETIRED_CARD_TEXT)],
    )
