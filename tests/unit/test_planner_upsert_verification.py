import pytest

pytest.importorskip("autogen_agentchat")

from zoneinfo import ZoneInfo

from fateforger.agents.schedular.agent import PlannerAgent
from fateforger.agents.schedular.messages import UpsertCalendarEvent


class _FakeTextResult:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeToolResult:
    def __init__(self, result) -> None:
        self.result = result


def test_extract_tool_payload_parses_json_text_result() -> None:
    result = _FakeToolResult(
        [_FakeTextResult('{"event":{"id":"evt1","htmlLink":"https://example.com"}}')]
    )
    payload = PlannerAgent._extract_tool_payload(result)
    assert isinstance(payload, dict)
    assert payload["event"]["id"] == "evt1"


def test_extract_tool_payload_raises_on_non_json_text_result() -> None:
    result = _FakeToolResult([_FakeTextResult("not-json")])
    with pytest.raises(RuntimeError):
        PlannerAgent._extract_tool_payload(result)


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
