from fateforger.slack_bot.task_cards import (
    FF_TASK_DETAILS_ACTION_ID,
    FF_TASK_VIEW_ALL_ACTION_ID,
    build_due_overview_blocks,
    decode_task_metadata,
    encode_task_metadata,
)


def test_task_metadata_roundtrip() -> None:
    encoded = encode_task_metadata(
        {"task_id": "T1", "project_id": "P1", "label": "TT-ABC12345"}
    )
    decoded = decode_task_metadata(encoded)
    assert decoded == {"task_id": "T1", "project_id": "P1", "label": "TT-ABC12345"}


def test_due_overview_blocks_include_details_and_view_all() -> None:
    tasks = [
        {
            "id": f"T{i}",
            "title": f"Task {i}",
            "project_id": "P1",
            "project_name": "Inbox",
            "due_date": "2026-03-02",
            "label": f"TT-AAAAAA{i}",
        }
        for i in range(1, 8)
    ]
    blocks = build_due_overview_blocks(
        tasks=tasks,
        due_date="2026-03-02",
        source_label="TickTick (all lists)",
        show_all=False,
        view_all_meta={"action": "view_all_due", "due_date": "2026-03-02"},
    )
    detail_buttons = [
        block.get("accessory", {})
        for block in blocks
        if block.get("type") == "section" and isinstance(block.get("accessory"), dict)
    ]
    assert any(button.get("action_id") == FF_TASK_DETAILS_ACTION_ID for button in detail_buttons)
    action_blocks = [block for block in blocks if block.get("type") == "actions"]
    assert action_blocks
    assert (
        action_blocks[0]["elements"][0]["action_id"] == FF_TASK_VIEW_ALL_ACTION_ID
    )
