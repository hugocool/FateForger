from __future__ import annotations

import json
from pathlib import Path

import pytest

from fateforger.core.calendar_preferences import CalendarAccountPrefs, CalendarPreferences


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    prefs = CalendarPreferences.load(tmp_path / "nonexistent.json")
    assert prefs.default_write_account is None
    assert prefs.default_write_calendar is None
    assert prefs.accounts == {}


def test_load_full_file(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "default_write_account": "work",
        "default_write_calendar": "hugo@work.com",
        "accounts": {
            "work": {
                "default_calendar": "hugo@work.com",
                "excluded_calendars": ["Holidays in NL"],
            },
            "personal": {
                "default_calendar": "hugo@gmail.com",
                "excluded_calendars": ["Birthdays"],
            },
        },
    }
    p = tmp_path / "calendar-preferences.json"
    p.write_text(json.dumps(data))
    prefs = CalendarPreferences.load(p)
    assert prefs.default_write_account == "work"
    assert prefs.default_write_calendar == "hugo@work.com"
    assert prefs.accounts["work"].excluded_calendars == ["Holidays in NL"]
    assert prefs.accounts["personal"].default_calendar == "hugo@gmail.com"


def test_load_partial_file_uses_defaults(tmp_path: Path) -> None:
    data = {"version": 1, "accounts": {"work": {}}}
    p = tmp_path / "calendar-preferences.json"
    p.write_text(json.dumps(data))
    prefs = CalendarPreferences.load(p)
    assert prefs.default_write_account is None
    assert prefs.accounts["work"].excluded_calendars == []
    assert prefs.accounts["work"].default_calendar is None


def test_excluded_for_account_unknown_account(tmp_path: Path) -> None:
    prefs = CalendarPreferences.load(tmp_path / "nonexistent.json")
    assert prefs.excluded_calendars_for("work") == []


def test_excluded_for_account_known_account(tmp_path: Path) -> None:
    data = {
        "accounts": {
            "work": {"excluded_calendars": ["Holidays"]},
        }
    }
    p = tmp_path / "prefs.json"
    p.write_text(json.dumps(data))
    prefs = CalendarPreferences.load(p)
    assert prefs.excluded_calendars_for("work") == ["Holidays"]


def test_load_invalid_json_returns_defaults(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json {{{")
    prefs = CalendarPreferences.load(p)
    assert prefs.default_write_account is None
    assert prefs.accounts == {}
