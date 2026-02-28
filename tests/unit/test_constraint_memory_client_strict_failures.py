from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fateforger.agents.timeboxing.mcp_clients import ConstraintMemoryClient


@pytest.mark.asyncio
async def test_call_tool_json_raises_on_tool_error_text() -> None:
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    client._workbench = SimpleNamespace(
        call_tool=AsyncMock(
            return_value=SimpleNamespace(
                to_text=lambda: "Error executing tool constraint_query_constraints: boom"
            )
        )
    )

    with pytest.raises(RuntimeError) as exc:
        await client._call_tool_json("constraint_query_constraints", arguments={})

    assert "constraint_query_constraints" in str(exc.value)


@pytest.mark.asyncio
async def test_call_tool_json_raises_on_non_json_text() -> None:
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    client._workbench = SimpleNamespace(
        call_tool=AsyncMock(return_value=SimpleNamespace(to_text=lambda: "not json"))
    )

    with pytest.raises(RuntimeError) as exc:
        await client._call_tool_json("constraint_get_store_info", arguments={})

    assert "constraint_get_store_info" in str(exc.value)


@pytest.mark.asyncio
async def test_close_stops_underlying_workbench() -> None:
    stop = AsyncMock()
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    client._workbench = SimpleNamespace(stop=stop)

    await client.close()

    stop.assert_awaited_once()
