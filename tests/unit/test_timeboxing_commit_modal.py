import pytest

pytest.importorskip("slack_bolt")

from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_MODAL_CALLBACK_ID,
    build_timebox_commit_modal,
)


def test_timebox_commit_modal_has_static_select_with_initial_option():
    view = build_timebox_commit_modal(
        suggested_date="2026-01-18",
        tz_name="Europe/Amsterdam",
        private_metadata="x=1",
    )
    assert view["type"] == "modal"
    assert view["callback_id"] == FF_TIMEBOX_COMMIT_MODAL_CALLBACK_ID
    blocks = view["blocks"]
    assert blocks and blocks[0]["type"] == "input"
    element = blocks[0]["element"]
    assert element["type"] == "static_select"
    assert element.get("initial_option") is not None
    assert element["initial_option"]["value"]
