"""Tests for EventDateTime.to_datetime() — the Pydantic replacement for _parse_event_dt."""

from __future__ import annotations

from datetime import date, datetime, timezone, time
from zoneinfo import ZoneInfo

import pytest

# Fixtures
UTC = timezone.utc
EASTERN = ZoneInfo("America/New_York")


class TestEventDateTimeToDatetime:
    """EventDateTime.to_datetime() replaces _parse_event_dt dict reads."""

    def _parse(self, raw: dict | None, tz=UTC) -> datetime | None:
        from fateforger.contracts import EventDateTime
        if raw is None:
            return None
        model = EventDateTime.model_validate(raw)
        return model.to_datetime(tz)

    # dateTime branch
    def test_datetime_utc(self):
        dt = self._parse({"dateTime": "2025-03-01T09:00:00Z"})
        assert dt is not None
        assert dt.hour == 9
        assert dt.tzinfo is not None

    def test_datetime_offset_converted_to_tz(self):
        dt = self._parse({"dateTime": "2025-03-01T09:00:00+05:00"}, tz=UTC)
        assert dt is not None
        assert dt.hour == 4  # 9 - 5 = 04:00 UTC

    def test_datetime_naive_gets_tz(self):
        dt = self._parse({"dateTime": "2025-03-01T09:00:00"}, tz=EASTERN)
        assert dt is not None
        assert dt.tzinfo is not None

    def test_datetime_field_used_when_both_present(self):
        dt = self._parse({"dateTime": "2025-03-01T09:00:00Z", "date": "2025-03-01"})
        assert dt is not None
        assert dt.hour == 9  # dateTime takes precedence

    # date branch  (all-day events)
    def test_all_day_date_returns_midnight(self):
        dt = self._parse({"date": "2025-03-01"}, tz=UTC)
        assert dt is not None
        assert dt.date() == date(2025, 3, 1)
        assert dt.hour == 0
        assert dt.minute == 0

    def test_all_day_date_respects_tz(self):
        dt = self._parse({"date": "2025-03-01"}, tz=EASTERN)
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.tzname() in ("EST", "EDT", "-05:00", "-04:00")

    def test_all_day_accepts_zoneinfo(self):
        tz = ZoneInfo("Europe/Amsterdam")
        dt = self._parse({"date": "2025-06-15"}, tz=tz)
        assert dt is not None
        assert dt.date() == date(2025, 6, 15)

    # empty / missing
    def test_none_returns_none(self):
        assert self._parse(None) is None

    def test_empty_dict_returns_none(self):
        assert self._parse({}) is None

    def test_unknown_keys_ignored(self):
        # extra keys should not raise
        dt = self._parse({"dateTime": "2025-03-01T09:00:00Z", "color": "green"})
        assert dt is not None


class TestEventDateTimeModelValidate:
    """EventDateTime can be constructed from aliased camelCase keys."""

    def test_camel_case_alias(self):
        from fateforger.contracts import EventDateTime
        m = EventDateTime.model_validate({"dateTime": "2025-03-01T09:00:00Z", "timeZone": "UTC"})
        assert m.date_time is not None
        assert m.time_zone == "UTC"

    def test_snake_case_direct(self):
        from fateforger.contracts import EventDateTime
        m = EventDateTime(date_time=datetime(2025, 3, 1, 9, 0, tzinfo=UTC))
        assert m.date_time is not None

    def test_date_field_parsed_from_string(self):
        from fateforger.contracts import EventDateTime
        m = EventDateTime.model_validate({"date": "2025-03-01"})
        assert m.date == date(2025, 3, 1)
