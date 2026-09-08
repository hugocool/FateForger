from types import SimpleNamespace

from fateforger.adapters.notion.timeboxing_preferences import CStatus, Necessity, Scope
from fateforger.agents.timeboxing.preferences import ConstraintScope, ConstraintStatus
from fateforger.slack_bot.constraint_review import (
    CONSTRAINT_DECISION_ACTION_ID,
    CONSTRAINT_DESCRIPTION_ACTION_ID,
    build_constraint_review_view,
    build_constraint_row_blocks,
    decode_metadata,
    parse_constraint_review_submission,
)


def test_constraint_review_view_accepts_uno_like_object():
    constraint = SimpleNamespace(
        uid="uid_1",
        name="  Sleep   window  ",
        description="Keep 8h sleep",
        necessity=Necessity.MUST,
        status=CStatus.LOCKED,
        scope=Scope.PROFILE,
    )
    view = build_constraint_review_view(
        constraint,
        channel_id="C1",
        thread_ts="123.456",
        user_id="U1",
    )
    assert view["type"] == "modal"
    assert view["title"]["text"] == "Constraint review"
    header = view["blocks"][0]["text"]["text"]
    assert "*Sleep window*" in header
    assert "(must)" in header
    assert "Scope: profile" in header


def test_constraint_review_view_sets_decline_initial_option():
    constraint = SimpleNamespace(
        id=42,
        name="No late meetings",
        description="",
        necessity="should",
        status=ConstraintStatus.DECLINED,
        scope=ConstraintScope.SESSION,
    )
    view = build_constraint_review_view(
        constraint,
        channel_id="C1",
        thread_ts="123.456",
        user_id="U1",
    )
    decision_block = next(
        block for block in view["blocks"] if block.get("block_id") == "constraint_decision_block"
    )
    element = decision_block["element"]
    assert element["action_id"] == CONSTRAINT_DECISION_ACTION_ID
    assert element["initial_option"]["value"] == "decline"


def test_constraint_review_metadata_round_trip_includes_constraint_id():
    constraint = SimpleNamespace(
        id=123,
        name="Constraint",
        description="",
        necessity="must",
        status=ConstraintStatus.PROPOSED,
        scope=ConstraintScope.SESSION,
    )
    view = build_constraint_review_view(
        constraint,
        channel_id="C123",
        thread_ts="111.222",
        user_id="U123",
    )
    metadata = decode_metadata(view["private_metadata"])
    assert metadata["constraint_id"] == "123"
    assert metadata["channel_id"] == "C123"
    assert metadata["thread_ts"] == "111.222"
    assert metadata["user_id"] == "U123"


def test_constraint_row_blocks_encode_review_button_metadata():
    constraints = [
        SimpleNamespace(
            id=1,
            name="One",
            description="Desc",
            necessity="must",
            status=ConstraintStatus.LOCKED,
            scope=ConstraintScope.SESSION,
        )
    ]
    blocks = build_constraint_row_blocks(
        constraints, thread_ts="999.000", user_id="U1", limit=20
    )
    assert blocks[0]["type"] == "section"
    button = blocks[0]["accessory"]
    meta = decode_metadata(button["value"])
    assert meta["constraint_id"] == "1"
    assert meta["thread_ts"] == "999.000"
    assert meta["user_id"] == "U1"


def test_parse_constraint_review_submission_extracts_status_and_description():
    state_values = {
        "constraint_decision_block": {
            CONSTRAINT_DECISION_ACTION_ID: {"selected_option": {"value": "accept"}}
        },
        "constraint_description_block": {
            CONSTRAINT_DESCRIPTION_ACTION_ID: {"value": "  hello  "}
        },
    }
    status, description = parse_constraint_review_submission(state_values)
    assert status == ConstraintStatus.LOCKED
    assert description == "hello"

