"""One answer for every button the legacy timeboxing agent left in Slack.

The ten action ids below were posted by that agent's stage and submit
cards, plus the constraint-review row/all buttons that
`constraint_review.py` (deleted) rendered on the memory-tool-result blocks
posted to legacy timeboxing session thread roots. The agent is gone; a
press must say so, not fail. The ids are kept verbatim because Slack will
keep sending them for as long as the messages exist.
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

# Rendered by constraint_review.py (deleted at 6f93212) on the
# memory-tool-result blocks it built for legacy timeboxing session thread
# roots: a per-row "Review" accessory button and a "Review all constraints"
# actions-block button. Their four Bolt listeners (two of them sharing one
# handler for the current and legacy "review all" ids) were deleted with
# that module; the view callback they opened is not listed here because a
# modal is unreachable once the button that opens it is inert.
CONSTRAINT_ROW_REVIEW_ACTION_ID = "timeboxing_constraint_review"
FF_CONSTRAINT_REVIEW_ALL_ACTION_ID = "ff_timeboxing_constraint_review_all"
LEGACY_CONSTRAINT_REVIEW_ALL_ACTION_ID = "timeboxing_constraint_review_all"

RETIRED_ACTION_IDS: tuple[str, ...] = (
    FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID,
    FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID,
    FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID,
    FF_TIMEBOX_STAGE_PROCEED_ACTION_ID,
    FF_TIMEBOX_STAGE_BACK_ACTION_ID,
    FF_TIMEBOX_STAGE_REDO_ACTION_ID,
    FF_TIMEBOX_STAGE_CANCEL_ACTION_ID,
    CONSTRAINT_ROW_REVIEW_ACTION_ID,
    FF_CONSTRAINT_REVIEW_ALL_ACTION_ID,
    LEGACY_CONSTRAINT_REVIEW_ALL_ACTION_ID,
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
