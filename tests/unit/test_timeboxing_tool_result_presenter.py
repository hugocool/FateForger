from __future__ import annotations

from fateforger.agents.timeboxing.tool_result_models import (
    InteractionMode,
    MemoryConstraintItem,
    MemoryToolResult,
)
from fateforger.agents.timeboxing.tool_result_presenter import (
    InteractionContext,
    present_memory_tool_result,
)


def test_present_memory_tool_result_text_mode() -> None:
    result = MemoryToolResult(action="list", ok=True, message="Memory updated")
    presentation = present_memory_tool_result(
        result=result,
        context=InteractionContext(
            mode=InteractionMode.TEXT,
            user_id="u_1",
            thread_ts="t_1",
        ),
    )
    assert presentation.payload["action"] == "list"
    assert presentation.blocks == []
    assert presentation.text_update == "Memory updated"


def test_present_memory_tool_result_slack_mode() -> None:
    result = MemoryToolResult(
        action="get",
        ok=True,
        message="Found 1 constraint.",
        constraints=[
            MemoryConstraintItem(
                uid="tb_1",
                name="Protect mornings",
                description="No meetings before noon",
                status="locked",
                scope="profile",
                source="user",
                used_this_session=True,
                needs_confirmation=True,
            )
        ],
    )
    presentation = present_memory_tool_result(
        result=result,
        context=InteractionContext(
            mode=InteractionMode.SLACK,
            user_id="u_1",
            thread_ts="t_1",
        ),
    )
    assert presentation.payload["action"] == "get"
    assert presentation.text_update is None
    assert presentation.blocks
    assert any("Memory" in block.get("text", {}).get("text", "") for block in presentation.blocks)
    block_texts = [
        block.get("text", {}).get("text", "")
        for block in presentation.blocks
        if isinstance(block, dict) and isinstance(block.get("text"), dict)
    ]
    assert any("source: user" in text for text in block_texts)
    assert any("used: this session" in text for text in block_texts)
