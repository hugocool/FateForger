"""Unit tests for timeboxing calendar MCP payload parsing."""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from fateforger.agents.timeboxing.mcp_clients import McpCalendarClient

pytest.importorskip("autogen_ext.tools.mcp")


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
        self.last_arguments: dict | None = None

    async def call_tool(self, name: str, arguments: dict) -> _FakeToolResult:
        """Return a deterministic list-events response."""
        assert name == "list-events"
        self.last_arguments = dict(arguments)
        return _FakeToolResult(json.dumps(self._payload))


class _SequenceWorkbench:
    """Fake workbench that replays deterministic call outcomes."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def call_tool(self, name: str, arguments: dict) -> Any:
        assert name == "list-events"
        self.calls += 1
        if not self._outcomes:
            raise AssertionError("No more fake outcomes available")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


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


class TestNormalizeEvents:
    """Full branch coverage for _normalize_events structural normalizer."""

    _ev = {
        "summary": "S",
        "start": {"dateTime": "2025-01-01T09:00:00Z"},
        "end": {"dateTime": "2025-01-01T10:00:00Z"},
    }

    def _call(self, payload: Any) -> list[dict[str, Any]]:
        return McpCalendarClient._normalize_events(payload)

    def test_dict_events_key(self) -> None:
        assert len(self._call({"events": [self._ev], "totalCount": 1})) == 1

    def test_dict_items_key(self) -> None:
        assert len(self._call({"items": [self._ev]})) == 1

    def test_dict_single_event_key(self) -> None:
        result = self._call({"event": self._ev})
        assert len(result) == 1 and result[0] is self._ev

    def test_dict_empty(self) -> None:
        assert self._call({}) == []

    def test_list_of_direct_events(self) -> None:
        assert len(self._call([self._ev, self._ev])) == 2

    def test_list_prefers_start_end_items(self) -> None:
        plain = {"foo": "bar"}
        result = self._call([plain, self._ev])
        # items with start/end are preferred
        assert all("start" in item for item in result)

    def test_list_nested_payload(self) -> None:
        nested = {"events": [self._ev]}
        result = self._call([nested])
        assert len(result) == 1

    def test_empty_list(self) -> None:
        assert self._call([]) == []

    def test_scalar_returns_empty(self) -> None:
        assert self._call("not-a-list") == []

    def test_none_returns_empty(self) -> None:
        assert self._call(None) == []

    def test_non_dict_items_in_list_are_skipped(self) -> None:
        # Non-dict items in a list should be ignored
        result = self._call([42, "string", self._ev])
        assert all(isinstance(item, dict) for item in result)


def test_extract_tool_payload_parses_wrapped_result_content() -> None:
    """Tool payload extraction should parse JSON from wrapped text content."""
    wrapped = _FakeWrappedResult('{"events":[{"summary":"Deep work"}]}')
    payload = McpCalendarClient._extract_tool_payload(wrapped)
    assert isinstance(payload, dict)
    assert payload.get("events")[0]["summary"] == "Deep work"


def test_extract_tool_payload_raises_on_non_json_text() -> None:
    """Tool payload extraction must fail loudly on invalid JSON text."""

    class _InvalidTextResult:
        def to_text(self) -> str:
            return "not-json"

    with pytest.raises(RuntimeError):
        McpCalendarClient._extract_tool_payload(_InvalidTextResult())


@pytest.mark.asyncio
async def test_list_day_immovables_reads_events_payload_shape() -> None:
    """list_day_immovables should return anchors from MCP ``events`` payloads."""
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = _FakeWorkbench(
        payload={
            "events": [
                {
                    "id": "test-brunch-event",
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


@pytest.mark.asyncio
async def test_list_day_snapshot_uses_iso_without_timezone_suffix() -> None:
    """list-events args should use MCP-compatible ISO datetime strings."""
    workbench = _FakeWorkbench(payload={"events": [], "totalCount": 0})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench

    await client.list_day_snapshot(
        calendar_id="primary",
        day=date(2026, 2, 14),
        tz=ZoneInfo("Europe/Amsterdam"),
        diagnostics={},
    )

    assert workbench.last_arguments is not None
    time_min = str(workbench.last_arguments["timeMin"])
    time_max = str(workbench.last_arguments["timeMax"])
    assert time_min == "2026-02-14T00:00:00"
    assert time_max == "2026-02-15T00:00:00"
    assert "+" not in time_min and "Z" not in time_min
    assert "+" not in time_max and "Z" not in time_max


@pytest.mark.asyncio
async def test_get_tools_raises_when_loader_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autogen_ext.tools.mcp as mcp_mod

    async def _empty_loader(_params):
        return []

    monkeypatch.setattr(mcp_mod, "mcp_server_tools", _empty_loader)
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._params = object()

    with pytest.raises(RuntimeError, match="calendar MCP server returned no tools"):
        await client.get_tools()


@pytest.mark.asyncio
async def test_list_day_snapshot_recovers_after_transport_connect_failure() -> None:
    """Client should reinitialize once after recoverable transport failure."""
    failing = _SequenceWorkbench([RuntimeError("All connection attempts failed")])
    healthy = _SequenceWorkbench([_FakeToolResult('{"events":[],"totalCount":0}')])
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = failing
    reset_calls = 0

    async def _reset_workbench() -> None:
        nonlocal reset_calls
        reset_calls += 1
        client._workbench = healthy

    client._reset_workbench = _reset_workbench  # type: ignore[method-assign]
    diagnostics: dict[str, object] = {}

    snapshot = await client.list_day_snapshot(
        calendar_id="primary",
        day=date(2026, 2, 14),
        tz=ZoneInfo("Europe/Amsterdam"),
        diagnostics=diagnostics,
    )

    assert snapshot.immovables == []
    assert reset_calls == 1
    assert failing.calls == 1
    assert healthy.calls == 1
    assert diagnostics["attempt_errors"][0]["recoverable"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_list_day_snapshot_recovers_after_actor_not_running_payload() -> None:
    """Client should retry once when MCP actor session is in a dead state."""
    failing = _SequenceWorkbench(
        [_FakeToolResult("MCP Actor not running, call initialize() first")]
    )
    healthy = _SequenceWorkbench([_FakeToolResult('{"events":[],"totalCount":0}')])
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = failing
    reset_calls = 0

    async def _reset_workbench() -> None:
        nonlocal reset_calls
        reset_calls += 1
        client._workbench = healthy

    client._reset_workbench = _reset_workbench  # type: ignore[method-assign]
    diagnostics: dict[str, object] = {}

    snapshot = await client.list_day_snapshot(
        calendar_id="primary",
        day=date(2026, 2, 14),
        tz=ZoneInfo("Europe/Amsterdam"),
        diagnostics=diagnostics,
    )

    assert snapshot.immovables == []
    assert reset_calls == 1
    assert failing.calls == 1
    assert healthy.calls == 1
    assert diagnostics["attempt_errors"][0]["recoverable"] is True  # type: ignore[index]


# ---------------------------------------------------------------------------
# Unit tests for _parse_event_dt (Pydantic EventDateTime dispatch)
# ---------------------------------------------------------------------------


class TestParseEventDt:
    """_parse_event_dt should delegate to EventDateTime.to_datetime()."""

    UTC = ZoneInfo("UTC")
    EASTERN = ZoneInfo("America/New_York")

    def _call(self, raw: dict[str, str] | None, tz: ZoneInfo | None = None):  # type: ignore[return]
        return McpCalendarClient._parse_event_dt(raw, tz=tz or self.UTC)

    def test_datetime_utc_z(self) -> None:
        dt = self._call({"dateTime": "2025-03-01T09:00:00Z"})
        assert dt is not None
        assert dt.hour == 9
        assert dt.tzinfo is not None

    def test_datetime_offset_converted(self) -> None:
        dt = self._call({"dateTime": "2025-03-01T09:00:00+05:00"}, tz=self.UTC)
        assert dt is not None
        assert dt.hour == 4  # 09:00+05:00 → 04:00Z

    def test_all_day_returns_midnight(self) -> None:
        from datetime import date as _date

        dt = self._call({"date": "2025-03-01"})
        assert dt is not None
        assert dt.date() == _date(2025, 3, 1)
        assert dt.hour == 0

    def test_none_returns_none(self) -> None:
        assert self._call(None) is None

    def test_empty_dict_returns_none(self) -> None:
        assert self._call({}) is None

    def test_all_day_respects_tz(self) -> None:
        dt = self._call({"date": "2025-06-01"}, tz=self.EASTERN)
        assert dt is not None
        assert dt.tzinfo is not None


# ── load_list() tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_list_empty_returns_empty_snapshot() -> None:
    """load_list with no calendars returns empty snapshot without calling workbench."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    workbench = _FakeWorkbench(payload={"events": [], "totalCount": 0})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[],
        write_calendar=CalendarEntry(account="work", calendar_id="primary"),
    )
    snapshot = await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    assert snapshot.response.events == []
    assert snapshot.immovables == []
    assert workbench.last_arguments is None  # workbench was never called


@pytest.mark.asyncio
async def test_load_list_single_calendar_passes_id_as_string() -> None:
    """Single calendar should pass calendarId as a plain string, not a JSON array."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    workbench = _FakeWorkbench(payload={"events": [], "totalCount": 0})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[CalendarEntry(account="work", calendar_id="work@gmail.com")],
        write_calendar=CalendarEntry(account="work", calendar_id="work@gmail.com"),
    )
    await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    assert workbench.last_arguments is not None
    assert workbench.last_arguments["calendarId"] == "work@gmail.com"


@pytest.mark.asyncio
async def test_load_list_multiple_calendars_passes_json_array() -> None:
    """Multiple calendars should pass calendarId as a JSON-encoded array string."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    workbench = _FakeWorkbench(payload={"events": [], "totalCount": 0})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[
            CalendarEntry(account="work", calendar_id="work@gmail.com"),
            CalendarEntry(account="personal", calendar_id="primary"),
        ],
        write_calendar=CalendarEntry(account="work", calendar_id="work@gmail.com"),
    )
    await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    assert workbench.last_arguments is not None
    ids = json.loads(workbench.last_arguments["calendarId"])
    assert ids == ["work@gmail.com", "primary"]


@pytest.mark.asyncio
async def test_load_list_deduplicates_by_event_id() -> None:
    """Events with duplicate IDs should be deduplicated; first occurrence wins."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    events = [
        {
            "id": "evt-1", "summary": "First copy",
            "start": {"dateTime": "2026-04-01T09:00:00"},
            "end": {"dateTime": "2026-04-01T10:00:00"},
        },
        {
            "id": "evt-1", "summary": "Duplicate",
            "start": {"dateTime": "2026-04-01T09:00:00"},
            "end": {"dateTime": "2026-04-01T10:00:00"},
        },
        {
            "id": "evt-2", "summary": "Unique",
            "start": {"dateTime": "2026-04-01T11:00:00"},
            "end": {"dateTime": "2026-04-01T12:00:00"},
        },
    ]
    workbench = _FakeWorkbench(payload={"events": events, "totalCount": len(events)})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[
            CalendarEntry(account="work", calendar_id="primary"),
            CalendarEntry(account="personal", calendar_id="other@gmail.com"),
        ],
        write_calendar=CalendarEntry(account="work", calendar_id="primary"),
    )
    snapshot = await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    event_ids = [e.id for e in snapshot.response.events]
    assert event_ids.count("evt-1") == 1
    summaries = [e.summary for e in snapshot.response.events if e.id == "evt-1"]
    assert summaries == ["First copy"]
    assert len(snapshot.response.events) == 2  # evt-1 (deduplicated) + evt-2
