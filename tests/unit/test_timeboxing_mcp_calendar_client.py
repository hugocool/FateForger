"""Unit tests for timeboxing calendar MCP payload parsing."""

from __future__ import annotations

import json
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from fateforger.agents.timeboxing.mcp_clients import McpCalendarClient


class _FakeToolResult:
    """Minimal MCP tool result with ``to_text`` for JSON payload parsing."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def to_text(self) -> str:
        """Return serialized payload text."""
        return self._payload


class _FakeWorkbench:
    """Fake MCP workbench for list-events calls."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def call_tool(self, name: str, arguments: dict) -> _FakeToolResult:
        """Return a deterministic list-events response."""
        _ = arguments
        assert name == "list-events"
        return _FakeToolResult(json.dumps(self._payload))


class _ResultTextItem:
    """Mimic MCP text payload item wrapper."""

    def __init__(self, content: str) -> None:
        self.content = content


class _FakeWrappedResult:
    """Mimic a tool result that stores JSON text under ``result[].content``."""

    def __init__(self, content: str) -> None:
        self.result = [_ResultTextItem(content)]


def test_normalize_events_accepts_events_key() -> None:
    """Calendar normalization should accept the ``events`` response shape."""
    payload = {"events": [{"summary": "Meeting"}], "totalCount": 1}
    events = McpCalendarClient._normalize_events(payload)
    assert len(events) == 1
    assert events[0]["summary"] == "Meeting"


def test_extract_tool_payload_parses_wrapped_result_content() -> None:
    """Tool payload extraction should parse JSON from wrapped text content."""
    wrapped = _FakeWrappedResult('{"events":[{"summary":"Deep work"}]}')
    payload = McpCalendarClient._extract_tool_payload(wrapped)
    assert isinstance(payload, dict)
    assert payload.get("events")[0]["summary"] == "Deep work"


@pytest.mark.asyncio
async def test_list_day_immovables_reads_events_payload_shape() -> None:
    """list_day_immovables should return anchors from MCP ``events`` payloads."""
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = _FakeWorkbench(
        payload={
            "events": [
                {
                    "summary": "Brunch",
                    "status": "confirmed",
                    "start": {"dateTime": "2026-02-14T11:30:00+01:00"},
                    "end": {"dateTime": "2026-02-14T13:00:00+01:00"},
                }
            ],
            "totalCount": 1,
        }
    )
    diagnostics: dict[str, object] = {}

    events = await client.list_day_immovables(
        calendar_id="primary",
        day=date(2026, 2, 14),
        tz=ZoneInfo("Europe/Amsterdam"),
        diagnostics=diagnostics,
    )

    assert events == [{"title": "Brunch", "start": "11:30", "end": "13:00"}]
    assert diagnostics.get("raw_event_count") == 1
    assert diagnostics.get("immovable_count") == 1
