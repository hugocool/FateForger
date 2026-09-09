"""A button from a retired flow says so, instead of failing forever.

The legacy agent's confirm/undo and stage cards can still be in Slack history.
Their old dispatchers sent to an agent that no longer exists inside a broad
``except`` that rewrote the card to "please try again" -- a missing agent
would have read as a transient glitch, every time, for as long as the card
existed.
"""

from __future__ import annotations

import pytest

from fateforger.slack_bot import retired_cards
from tests.doubles.slack import RecordingSlackClient


@pytest.mark.parametrize("action_id", retired_cards.RETIRED_ACTION_IDS)
async def test_pressing_a_retired_card_rewrites_it_in_place(action_id):
    client = RecordingSlackClient()
    body = {
        "channel": {"id": "C1"},
        "message": {"ts": "100.1"},
        "actions": [{"action_id": action_id, "value": "anything"}],
    }

    await retired_cards.retire_card(client=client, body=body)

    assert len(client.updates) == 1
    update = client.updates[0]
    assert (update["channel"], update["ts"]) == ("C1", "100.1")
    assert update["text"] == retired_cards.RETIRED_CARD_TEXT
    assert update["blocks"][0]["text"]["text"] == retired_cards.RETIRED_CARD_TEXT


async def test_a_press_without_a_message_is_ignored_not_raised():
    client = RecordingSlackClient()
    await retired_cards.retire_card(client=client, body={"actions": [{}]})
    assert client.updates == []


def test_the_ten_legacy_action_ids_are_all_covered():
    assert set(retired_cards.RETIRED_ACTION_IDS) == {
        "ff_timebox_confirm_submit",
        "ff_timebox_cancel_submit",
        "ff_timebox_undo_submit",
        "ff_timebox_stage_proceed",
        "ff_timebox_stage_back",
        "ff_timebox_stage_redo",
        "ff_timebox_stage_cancel",
        "timeboxing_constraint_review",
        "ff_timeboxing_constraint_review_all",
        "timeboxing_constraint_review_all",
    }
