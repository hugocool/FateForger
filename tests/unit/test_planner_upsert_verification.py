import datetime as dt
import json
import re

import pytest

pytest.importorskip("autogen_agentchat")

from zoneinfo import ZoneInfo

from fateforger.agents.schedular.agent import PlannerAgent
from fateforger.agents.schedular.messages import SuggestNextSlot
from fateforger.agents.schedular.messages import UpsertCalendarEvent


class _FakeTextResult:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeToolResult:
    def __init__(self, result) -> None:
        self.result = result


class _FakeWorkbench:
    def __init__(self, payload_text: str) -> None:
        self._payload_text = payload_text
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return _FakeToolResult([_FakeTextResult(self._payload_text)])


class _FakeUpsertWorkbench:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._get_calls = 0

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name == "get-event":
            self._get_calls += 1
            return _FakeToolResult(
                [
                    _FakeTextResult(
                        json.dumps(
                            {
                                "event": {
                                    "id": "evt-1",
                                    "summary": "Daily planning session",
                                    "start": {
                                        "dateTime": "2026-02-27T18:20:51+01:00"
                                    },
                                    "end": {
                                        "dateTime": "2026-02-27T18:50:51+01:00"
                                    },
                                    "htmlLink": "https://example.com/e/evt-1",
                                }
                            }
                        )
                    )
                ]
            )
        if name == "update-event":
            return _FakeToolResult([_FakeTextResult(json.dumps({"ok": True}))])
        raise AssertionError(f"Unexpected tool call: {name}")


class _DummyHaunt:
    def register_agent(self, *args, **kwargs):
        return None

    async def record_envelope(self, *args, **kwargs):
        return None


def test_extract_tool_payload_parses_json_text_result() -> None:
    result = _FakeToolResult(
        [_FakeTextResult('{"event":{"id":"evt1","htmlLink":"https://example.com"}}')]
    )
    payload = PlannerAgent._extract_tool_payload(result)
    assert isinstance(payload, dict)
    assert payload["event"]["id"] == "evt1"


def test_extract_tool_payload_coerces_non_json_text_result_to_error_payload() -> None:
    result = _FakeToolResult([_FakeTextResult("MCP error -32602: Invalid arguments")])
    payload = PlannerAgent._extract_tool_payload(result)
    assert isinstance(payload, dict)
    assert payload["ok"] is False
    assert "MCP error -32602" in str(payload["error"])
    assert (
        PlannerAgent._extract_tool_error(payload) == "MCP error -32602: Invalid arguments"
    )


def test_calendar_tool_datetime_strips_offset_and_microseconds() -> None:
    amsterdam = dt.timezone(dt.timedelta(hours=1))
    value = dt.datetime(2026, 2, 27, 9, 15, 10, 987654, tzinfo=amsterdam)
    assert PlannerAgent._calendar_tool_datetime(value) == "2026-02-27T09:15:10"


def test_calendar_event_datetime_arg_localizes_and_strips_offset() -> None:
    assert (
        PlannerAgent._calendar_event_datetime_arg(
            "2026-02-27T17:20:51.252588+00:00", time_zone="Europe/Amsterdam"
        )
        == "2026-02-27T18:20:51"
    )
    assert (
        PlannerAgent._calendar_event_datetime_arg(
            "2026-02-27", time_zone="Europe/Amsterdam"
        )
        == "2026-02-27"
    )


@pytest.mark.asyncio
async def test_suggest_next_slot_uses_mcp_datetime_shape_without_offset() -> None:
    workbench = _FakeWorkbench(payload_text=json.dumps({"events": []}))
    agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
    agent._workbench = workbench
    result = await agent.handle_suggest_next_slot(
        SuggestNextSlot(
            calendar_id="primary",
            duration_min=30,
            time_zone="Europe/Amsterdam",
            horizon_days=2,
            work_start_hour=0,
            work_end_hour=23,
        ),
        None,
    )
    assert result.ok is True
    assert len(workbench.calls) == 1
    _, args = workbench.calls[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", args["timeMin"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", args["timeMax"])
    assert "+" not in args["timeMin"]
    assert "Z" not in args["timeMin"]


@pytest.mark.asyncio
async def test_suggest_next_slot_handles_plain_text_mcp_error_without_crashing() -> None:
    workbench = _FakeWorkbench(
        payload_text=(
            "MCP error -32602: Invalid arguments for tool list-events: "
            "[{\"path\": [\"timeMin\"], \"message\": \"bad format\"}]"
        )
    )
    agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
    agent._workbench = workbench
    result = await agent.handle_suggest_next_slot(
        SuggestNextSlot(
            calendar_id="primary",
            duration_min=30,
            time_zone="Europe/Amsterdam",
            horizon_days=1,
            work_start_hour=0,
            work_end_hour=23,
        ),
        None,
    )
    assert result.ok is False
    assert result.error == "No free slot found"


@pytest.mark.asyncio
async def test_upsert_calendar_event_normalizes_start_end_for_update_event() -> None:
    workbench = _FakeUpsertWorkbench()
    agent = PlannerAgent("planner_agent", haunt=_DummyHaunt())
    agent._workbench = workbench
    result = await agent.handle_upsert_calendar_event(
        UpsertCalendarEvent(
            calendar_id="primary",
            event_id="evt-1",
            summary="Daily planning session",
            description="Plan tomorrow",
            start="2026-02-27T17:20:51.252588+00:00",
            end="2026-02-27T17:50:51.252588+00:00",
            time_zone="Europe/Amsterdam",
            color_id="10",
        ),
        None,
    )
    assert result.ok is True
    update_calls = [call for call in workbench.calls if call[0] == "update-event"]
    assert len(update_calls) == 1
    update_args = update_calls[0][1]
    assert update_args["start"] == "2026-02-27T18:20:51"
    assert update_args["end"] == "2026-02-27T18:50:51"


def test_extract_tool_error_detects_structured_failure() -> None:
    error = PlannerAgent._extract_tool_error(
        {"success": False, "error": "permission denied"}
    )
    assert error == "permission denied"


def test_event_matches_upsert_request_success() -> None:
    message = UpsertCalendarEvent(
        calendar_id="primary",
        event_id="evt1",
        summary="Daily planning session",
        description="Plan tomorrow",
        start="2026-02-17T13:49:00+01:00",
        end="2026-02-17T14:19:00+01:00",
        time_zone="Europe/Amsterdam",
        color_id="10",
    )
    ok, reason = PlannerAgent._event_matches_upsert_request(
        event={
            "id": "evt1",
            "summary": "Daily planning session",
            "start": {"dateTime": "2026-02-17T13:49:00+01:00"},
            "end": {"dateTime": "2026-02-17T14:19:00+01:00"},
        },
        message=message,
        tz=ZoneInfo("Europe/Amsterdam"),
    )
    assert ok is True
    assert reason is None


def test_event_matches_upsert_request_detects_mismatch() -> None:
    message = UpsertCalendarEvent(
        calendar_id="primary",
        event_id="evt1",
        summary="Daily planning session",
        description="Plan tomorrow",
        start="2026-02-17T13:49:00+01:00",
        end="2026-02-17T14:19:00+01:00",
        time_zone="Europe/Amsterdam",
        color_id="10",
    )
    ok, reason = PlannerAgent._event_matches_upsert_request(
        event={
            "id": "evt1",
            "summary": "Daily planning session",
            "start": {"dateTime": "2026-02-14T17:30:00+01:00"},
            "end": {"dateTime": "2026-02-14T18:00:00+01:00"},
        },
        message=message,
        tz=ZoneInfo("Europe/Amsterdam"),
    )
    assert ok is False
    assert reason == "start mismatch after upsert verification"
