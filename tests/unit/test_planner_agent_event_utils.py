"""Unit tests for PlannerAgent static event-utility helpers.

Covers _normalize_events, _normalize_event, and _parse_event_dt before and
after the refactoring to Pydantic dispatch to prevent regressions.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.schedular.agent import PlannerAgent

UTC = ZoneInfo("UTC")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

_EV = {
    "id": "1",
    "summary": "Stand-up",
    "start": {"dateTime": "2025-03-01T09:00:00Z"},
    "end": {"dateTime": "2025-03-01T09:30:00Z"},
}


# ---------------------------------------------------------------------------
# _normalize_events
# ---------------------------------------------------------------------------


class TestPlannerNormalizeEvents:
    def _call(self, payload) -> list[dict]:
        return PlannerAgent._normalize_events(payload)

    def test_items_key(self) -> None:
        result = self._call({"items": [_EV]})
        assert len(result) == 1

    def test_events_key(self) -> None:
        result = self._call({"events": [_EV]})
        assert len(result) == 1

    def test_plain_list(self) -> None:
        result = self._call([_EV, _EV])
        assert len(result) == 2

    def test_empty_dict_returns_empty(self) -> None:
        assert self._call({}) == []

    def test_empty_list_returns_empty(self) -> None:
        assert self._call([]) == []

    def test_scalar_returns_empty(self) -> None:
        assert self._call("nope") == []

    def test_non_dict_items_in_list_skipped(self) -> None:
        result = self._call([_EV, 42, "string"])
        assert result == [_EV]


# ---------------------------------------------------------------------------
# _normalize_event
# ---------------------------------------------------------------------------


class TestPlannerNormalizeEvent:
    def _call(self, payload) -> dict | None:
        return PlannerAgent._normalize_event(payload)

    def test_dict_with_id(self) -> None:
        assert self._call({"id": "1"}) == {"id": "1"}

    def test_dict_with_summary(self) -> None:
        assert self._call({"summary": "x"}) == {"summary": "x"}

    def test_event_wrapper(self) -> None:
        assert self._call({"event": _EV}) == _EV

    def test_item_wrapper(self) -> None:
        assert self._call({"item": _EV}) == _EV

    def test_items_list_returns_first(self) -> None:
        result = self._call({"items": [_EV, {"id": "2"}]})
        assert result == _EV

    def test_list_returns_first_dict(self) -> None:
        result = self._call([_EV])
        assert result == _EV

    def test_none_returns_none(self) -> None:
        assert self._call(None) is None

    def test_unrecognised_dict_returns_none(self) -> None:
        assert self._call({"foo": "bar"}) is None


# ---------------------------------------------------------------------------
# _parse_event_dt
# ---------------------------------------------------------------------------


class TestPlannerParseEventDt:
    def _call(self, raw, tz: ZoneInfo = UTC) -> dt.datetime | None:
        return PlannerAgent._parse_event_dt(raw, tz=tz)

    def test_none_returns_none(self) -> None:
        assert self._call(None) is None

    def test_empty_str_returns_none(self) -> None:
        assert self._call("") is None

    def test_empty_dict_returns_none(self) -> None:
        assert self._call({}) is None

    def test_iso_string_utc(self) -> None:
        result = self._call("2025-03-01T09:00:00Z")
        assert result is not None
        assert result.year == 2025 and result.hour == 9

    def test_iso_string_no_tz_uses_tz_arg(self) -> None:
        result = self._call("2025-03-01T09:00:00", AMSTERDAM)
        assert result is not None
        assert result.tzinfo is not None

    def test_dict_datetime_field(self) -> None:
        result = self._call({"dateTime": "2025-03-01T09:00:00Z"})
        assert result is not None
        assert result.hour == 9

    def test_dict_date_field_midnight(self) -> None:
        result = self._call({"date": "2025-03-01"})
        assert result is not None
        assert result.date() == dt.date(2025, 3, 1)
        assert result.hour == 0

    def test_dict_offset_converted_to_tz(self) -> None:
        result = self._call({"dateTime": "2025-03-01T09:00:00+05:00"}, UTC)
        assert result is not None
        assert result.hour == 4  # 09:00+05:00 → 04:00Z
