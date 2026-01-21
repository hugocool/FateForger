import pytest

pytest.importorskip("slack_bolt")

from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    build_timebox_commit_prompt_message,
)


def test_timebox_commit_prompt_has_static_select_with_initial_option():
    msg = build_timebox_commit_prompt_message(
        planned_date="2026-01-18",
        tz_name="Europe/Amsterdam",
        meta_value="x=1",
    )
    assert msg.text
    blocks = msg.blocks
    assert blocks and blocks[0]["type"] == "section"
    actions = blocks[1]
    assert actions["type"] == "actions"
    elements = actions["elements"]
    assert len(elements) == 2
    element = elements[0]
    assert element["type"] == "static_select"
    assert element["action_id"] == FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID
    assert element.get("initial_option") is not None
    assert element["initial_option"]["value"]
    confirm = elements[1]
    assert confirm["type"] == "button"
    assert confirm["action_id"] == FF_TIMEBOX_COMMIT_START_ACTION_ID
    assert confirm["text"]["text"] == "Confirm"
