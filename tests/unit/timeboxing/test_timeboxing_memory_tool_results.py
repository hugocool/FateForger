from __future__ import annotations

from fateforger.agents.timeboxing.tool_result_models import (
    MemoryConstraintItem,
    MemoryToolResult,
)


def test_memory_constraint_item_from_nested_constraint_record() -> None:
    item = MemoryConstraintItem.from_payload(
        {
            "uid": "tb_1",
            "constraint_record": {
                "name": "Deep work mornings",
                "description": "Protect 09:00-12:00 for deep work",
                "necessity": "should",
                "status": "proposed",
                "scope": "profile",
                "source": "system",
                "confidence": 0.52,
                "selector": {"needs_confirmation": True},
            },
        }
    )
    assert item is not None
    assert item.uid == "tb_1"
    assert item.needs_confirmation is True
    assert item.status == "proposed"
    assert item.scope == "profile"
    assert item.source == "system"
    assert item.used_this_session is False


def test_memory_tool_result_serializes_constraints() -> None:
    result = MemoryToolResult(
        action="get",
        ok=True,
        uid="tb_2",
        constraints=[
            MemoryConstraintItem(
                uid="tb_2",
                name="No late calls",
                description="Avoid calls after 18:00",
                status="locked",
                scope="profile",
                source="user",
                used_this_session=True,
            )
        ],
    )
    payload = result.to_tool_payload()
    assert payload["action"] == "get"
    assert payload["ok"] is True
    assert payload["constraints"][0]["uid"] == "tb_2"
    assert payload["constraints"][0]["used_this_session"] is True
