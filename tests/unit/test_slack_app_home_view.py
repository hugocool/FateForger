import pytest

pytest.importorskip("slack_bolt")

from fateforger.slack_bot.handlers import FF_APPHOME_WEEKLY_REVIEW_ACTION_ID, _build_app_home_view


def test_app_home_view_includes_focus_and_quick_action():
    view = _build_app_home_view(user_id="U1", focus_agent="timeboxing_agent")
    assert view["type"] == "home"
    blocks = view["blocks"]
    assert any(b.get("type") == "header" for b in blocks)
    assert any("timeboxing_agent" in str(b) for b in blocks)
    assert any(
        b.get("type") == "actions"
        and any(
            el.get("action_id") == FF_APPHOME_WEEKLY_REVIEW_ACTION_ID
            for el in b.get("elements", [])
        )
        for b in blocks
    )

