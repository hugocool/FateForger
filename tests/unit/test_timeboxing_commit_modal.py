import pytest

pytest.importorskip("slack_bolt")

from datetime import datetime, timezone

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


def test_timebox_commit_prompt_includes_weekends(monkeypatch: pytest.MonkeyPatch) -> None:
    import fateforger.slack_bot.timeboxing_commit as commit_mod

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 1, 16, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(commit_mod, "datetime", _FixedDateTime)

    msg = build_timebox_commit_prompt_message(
        planned_date="2026-01-17",
        tz_name="UTC",
        meta_value="x=1",
    )

    blocks = msg.blocks
    assert blocks and blocks[1]["type"] == "actions"
    select = blocks[1]["elements"][0]
    option_values = [opt["value"] for opt in select.get("options", [])]
    # Friday 2026-01-16 → next two days include Sat/Sun.
    assert "2026-01-17" in option_values
    assert "2026-01-18" in option_values
