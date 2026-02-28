"""Unit tests for reconcile.py module-level event utility functions.

These cover the three pure helpers that extract/parse calendar payloads from
MCP tool results so that they can be refactored from dict-probing to Pydantic-
backed dispatch without regressions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

from fateforger.haunt.reconcile import (
    _event_within_window,
    _extract_tool_payload,
    _normalize_event,
    _normalize_events,
    _parse_event_dt,
)

UTC = timezone.utc
PLUS5 = timezone(timedelta := __import__("datetime").timedelta(hours=5))


# ---------------------------------------------------------------------------
# _parse_event_dt
# ---------------------------------------------------------------------------


class TestParseEventDtReconcile:
    """_parse_event_dt in reconcile.py handles str | dict | None inputs."""

    tz = UTC

    def _call(self, raw: Any) -> datetime | None:
        return _parse_event_dt(raw, tz=self.tz)

    def test_none_returns_none(self) -> None:
        assert self._call(None) is None

    def test_str_utc_z(self) -> None:
        dt = self._call("2025-03-01T09:00:00Z")
        assert dt is not None
        assert dt.year == 2025 and dt.month == 3 and dt.day == 1
        assert dt.hour == 9

    def test_str_no_tz_uses_tz_arg(self) -> None:
        dt = self._call("2025-03-01T09:00:00")
        assert dt is not None
        assert dt.tzinfo == self.tz

    def test_str_invalid_returns_none(self) -> None:
        assert self._call("not-a-date") is None

    def test_dict_date_time_field(self) -> None:
        dt = self._call({"dateTime": "2025-03-01T09:00:00Z"})
        assert dt is not None
        assert dt.hour == 9

    def test_dict_date_field_midnight(self) -> None:
        dt = self._call({"date": "2025-03-01"})
        assert dt is not None
        assert dt.date() == date(2025, 3, 1)
        assert dt.hour == 0

    def test_dict_empty_returns_none(self) -> None:
        assert self._call({}) is None

    def test_unsupported_type_returns_none(self) -> None:
        assert self._call(42) is None

    def test_dict_offset_converted(self) -> None:
        dt = self._call({"dateTime": "2025-03-01T09:00:00+05:00"})
        assert dt is not None
        assert dt.utcoffset() is not None


# ---------------------------------------------------------------------------
# _normalize_events
# ---------------------------------------------------------------------------


class TestNormalizeEventsReconcile:
    """_normalize_events extracts flat event-dict lists from various shapes."""

    _ev = {"id": "1", "summary": "Meeting"}

    def _call(self, payload: Any) -> list[dict]:
        return _normalize_events(payload)

    def test_dict_events_key(self) -> None:
        assert len(self._call({"events": [self._ev]})) == 1

    def test_dict_items_key(self) -> None:
        assert len(self._call({"items": [self._ev]})) == 1

    def test_dict_no_matching_key_returns_empty(self) -> None:
        assert self._call({"other": [self._ev]}) == []

    def test_plain_list(self) -> None:
        assert len(self._call([self._ev, self._ev])) == 2

    def test_non_dict_items_in_list_skipped(self) -> None:
        result = self._call([self._ev, 42, "string"])
        assert result == [self._ev]

    def test_empty_list(self) -> None:
        assert self._call([]) == []

    def test_scalar_returns_empty(self) -> None:
        assert self._call("not-a-collection") == []

    def test_none_returns_empty(self) -> None:
        assert self._call(None) == []


# ---------------------------------------------------------------------------
# _normalize_event
# ---------------------------------------------------------------------------


class TestNormalizeEvent:
    """_normalize_event extracts a single event dict from various wrappers."""

    _ev = {"id": "1", "summary": "Meeting"}

    def _call(self, payload: Any) -> dict | None:
        return _normalize_event(payload)

    def test_direct_dict_with_id(self) -> None:
        assert self._call({"id": "1"}) == {"id": "1"}

    def test_direct_dict_with_summary(self) -> None:
        assert self._call({"summary": "s"}) == {"summary": "s"}

    def test_event_wrapper(self) -> None:
        assert self._call({"event": self._ev}) == self._ev

    def test_item_wrapper(self) -> None:
        assert self._call({"item": self._ev}) == self._ev

    def test_unrecognised_dict_returns_none(self) -> None:
        assert self._call({"foo": "bar"}) is None

    def test_none_returns_none(self) -> None:
        assert self._call(None) is None

    def test_list_returns_none(self) -> None:
        assert self._call([self._ev]) is None


# ---------------------------------------------------------------------------
# _extract_tool_payload
# ---------------------------------------------------------------------------


class _Obj:
    """Minimal object that mimics MCP ToolResult duck type."""
    def __init__(self, *, result=None, content=None):
        if result is not None:
            self.result = result
        if content is not None:
            self.content = content


class _TextContent:
    """Object that mimics TextResultContent with a .content str."""
    def __init__(self, content: str):
        self.content = content


class TestExtractToolPayload:
    """_extract_tool_payload normalises MCP result objects to plain dicts/lists."""

    def _call(self, result: Any) -> Any:
        return _extract_tool_payload(result)

    def test_dict_passthrough(self) -> None:
        d = {"id": "1"}
        assert self._call(d) is d

    def test_result_list_with_content_str_decodes_json(self) -> None:
        import json
        payload = {"events": []}
        content_obj = _TextContent(json.dumps(payload))
        result = self._call(_Obj(result=[content_obj]))
        assert result == payload

    def test_result_list_with_invalid_json_falls_back(self) -> None:
        content_obj = _TextContent("not-json")
        result = self._call(_Obj(result=[content_obj]))
        # Should still return something (not raise)
        assert result is not None

    def test_result_list_with_no_content_returns_list(self) -> None:
        items = [{"id": "1"}]
        result = self._call(_Obj(result=items))
        assert result == items

    def test_content_str_decodes_json(self) -> None:
        import json
        payload = {"events": [{}]}
        result = self._call(_Obj(content=json.dumps(payload)))
        assert result == payload

    def test_content_str_invalid_json_falls_back(self) -> None:
        result = self._call(_Obj(content="plain text"))
        assert result is not None

    def test_content_non_str_returned_as_is(self) -> None:
        items = [1, 2]
        result = self._call(_Obj(content=items))
        assert result == items

    def test_no_attributes_returns_empty_dict(self) -> None:
        class _Empty:
            pass
        assert self._call(_Empty()) == {}