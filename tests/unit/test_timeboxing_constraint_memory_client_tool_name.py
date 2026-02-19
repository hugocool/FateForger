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
        self._result: object = _DummyResult("[]")

    async def call_tool(self, name: str, arguments: dict) -> _DummyResult:
        self.calls.append((name, arguments))
        return self._result


class _DummyContentItem:
    def __init__(self, content: str) -> None:
        self.content = content


class _DummyToolResult:
    def __init__(
        self, *, text: str = "", result: list[object] | None = None, is_error: bool = False
    ) -> None:
        self._text = text
        self.result = result if result is not None else []
        self.is_error = is_error

    def to_text(self) -> str:
        return self._text


class _FlakyTimeoutWorkbench:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> object:
        self.calls.append((name, arguments))
        if len(self.calls) == 1:
            return _DummyToolResult(
                text=(
                    "Timed out while waiting for response to ClientRequest. "
                    "Waited 10.0 seconds."
                )
            )
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


@pytest.mark.asyncio
async def test_query_types_accepts_empty_structured_payload() -> None:
    """Structured tool result with empty result[] should decode to []."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    wb = _DummyWorkbench()
    wb._result = _DummyToolResult(text="", result=[])
    client._workbench = wb

    out = await ConstraintMemoryClient.query_types(client, stage="CollectConstraints")
    assert out == []


@pytest.mark.asyncio
async def test_query_constraints_parses_multiple_structured_json_objects() -> None:
    """Structured result[] entries with JSON object content should decode as list rows."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    wb = _DummyWorkbench()
    wb._result = _DummyToolResult(
        result=[
            _DummyContentItem('{"uid":"u1","name":"Sleep"}'),
            _DummyContentItem('{"uid":"u2","name":"Work window"}'),
        ]
    )
    client._workbench = wb

    out = await ConstraintMemoryClient.query_constraints(
        client,
        filters={},
        type_ids=None,
        tags=None,
        sort=None,
        limit=10,
    )
    assert [item["uid"] for item in out] == ["u1", "u2"]


@pytest.mark.asyncio
async def test_query_constraints_parses_newline_delimited_json_objects() -> None:
    """Text payload with concatenated JSON objects should decode as list rows."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    wb = _DummyWorkbench()
    wb._result = _DummyToolResult(
        text='{"uid":"u1","name":"Sleep"}\n{"uid":"u2","name":"Work window"}'
    )
    client._workbench = wb

    out = await ConstraintMemoryClient.query_constraints(
        client,
        filters={},
        type_ids=None,
        tags=None,
        sort=None,
        limit=10,
    )
    assert [item["uid"] for item in out] == ["u1", "u2"]


@pytest.mark.asyncio
async def test_query_constraints_raises_loudly_when_tool_marks_error() -> None:
    """is_error=True should raise a RuntimeError with tool name + payload."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    wb = _DummyWorkbench()
    wb._result = _DummyToolResult(text="backend unavailable", is_error=True)
    client._workbench = wb

    with pytest.raises(RuntimeError) as exc:
        await ConstraintMemoryClient.query_constraints(
            client,
            filters={},
            type_ids=None,
            tags=None,
            sort=None,
            limit=10,
        )
    assert "constraint_query_constraints" in str(exc.value)
    assert "backend unavailable" in str(exc.value)


@pytest.mark.asyncio
async def test_query_constraints_raises_loudly_on_structured_error_payload() -> None:
    """is_error=True with result[] text should raise RuntimeError with payload text."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    wb = _DummyWorkbench()
    wb._result = _DummyToolResult(
        text="",
        result=[_DummyContentItem("Timed out while waiting for response to ClientRequest.")],
        is_error=True,
    )
    client._workbench = wb

    with pytest.raises(RuntimeError) as exc:
        await ConstraintMemoryClient.query_constraints(
            client,
            filters={},
            type_ids=None,
            tags=None,
            sort=None,
            limit=10,
        )
    assert "constraint_query_constraints" in str(exc.value)
    assert "Timed out while waiting for response" in str(exc.value)


@pytest.mark.asyncio
async def test_query_types_retries_once_on_mcp_timeout_text() -> None:
    """Timeout-shaped MCP text should trigger one retry before succeeding."""
    client = ConstraintMemoryClient.__new__(ConstraintMemoryClient)
    wb = _FlakyTimeoutWorkbench()
    client._workbench = wb

    out = await ConstraintMemoryClient.query_types(client, stage="CollectConstraints")
    assert out == []
    assert len(wb.calls) == 2
