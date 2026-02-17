import pytest

from fateforger.agents.timeboxing.mcp_clients import ConstraintMemoryClient


class _DummyResult:
    def __init__(self, text: str) -> None:
        self._text = text

    def to_text(self) -> str:
        return self._text


class _DummyWorkbench:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> _DummyResult:
        self.calls.append((name, arguments))
        return _DummyResult("[]")


@pytest.mark.asyncio
async def test_constraint_memory_client_uses_openai_safe_tool_name() -> None:
    """Pin that the direct MCP client calls the underscore tool name (not dots)."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    client._workbench = _DummyWorkbench()

    out = await ConstraintMemoryClient.query_constraints(
        client,
        filters={"as_of": "2026-01-01"},
        type_ids=None,
        tags=None,
        sort=None,
        limit=10,
    )
    assert out == []
    assert client._workbench.calls[0][0] == "constraint_query_constraints"


@pytest.mark.asyncio
async def test_constraint_memory_client_upsert_uses_openai_safe_tool_name() -> None:
    """Pin that durable upserts call the underscore MCP tool name."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    client._workbench = _DummyWorkbench()

    with pytest.raises(RuntimeError):
        await ConstraintMemoryClient.upsert_constraint(
            client,
            record={"constraint_record": {"name": "x"}},
            event={"action": "upsert"},
        )
    assert client._workbench.calls[0][0] == "constraint_upsert_constraint"
