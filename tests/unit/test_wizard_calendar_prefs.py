from __future__ import annotations

import json
from pathlib import Path

from fateforger.setup_wizard.calendar_prefs import read_accounts, read_prefs, write_prefs

TOKENS_MULTI = {
    "work": {
        "access_token": "tok1",
        "refresh_token": "ref1",
        "cached_email": "hugo@work.com",
        "cached_calendars": [],
    },
    "personal": {
        "access_token": "tok2",
        "refresh_token": "ref2",
        "cached_email": "hugo@gmail.com",
        "cached_calendars": [],
    },
}

TOKENS_LEGACY = {
    "access_token": "tok1",
    "refresh_token": "ref1",
}


def test_read_accounts_multi(tmp_path: Path) -> None:
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(TOKENS_MULTI))
    accounts = read_accounts(p)
    assert set(accounts.keys()) == {"work", "personal"}
    assert accounts["work"]["cached_email"] == "hugo@work.com"


def test_read_accounts_legacy_single(tmp_path: Path) -> None:
    """Legacy format (bare token object) returns a single 'default' account."""
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(TOKENS_LEGACY))
    accounts = read_accounts(p)
    assert "default" in accounts


def test_read_accounts_missing_file(tmp_path: Path) -> None:
    accounts = read_accounts(tmp_path / "nonexistent.json")
    assert accounts == {}


def test_read_accounts_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "tokens.json"
    p.write_text("not valid {{{")
    assert read_accounts(p) == {}


def test_read_prefs_missing(tmp_path: Path) -> None:
    prefs = read_prefs(tmp_path / "calendar-preferences.json")
    assert prefs["default_write_account"] is None
    assert prefs["accounts"] == {}


def test_write_and_read_prefs_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "calendar-preferences.json"
    data = {
        "version": 1,
        "default_write_account": "work",
        "default_write_calendar": "hugo@work.com",
        "accounts": {
            "work": {
                "default_calendar": "hugo@work.com",
                "excluded_calendars": ["Holidays"],
            },
        },
    }
    write_prefs(p, data)
    loaded = read_prefs(p)
    assert loaded["default_write_account"] == "work"
    assert loaded["accounts"]["work"]["excluded_calendars"] == ["Holidays"]


def test_write_prefs_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "subdir" / "nested" / "prefs.json"
    write_prefs(p, {"version": 1})
    assert p.exists()


def test_read_prefs_bad_json_returns_defaults(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json")
    prefs = read_prefs(p)
    assert prefs["default_write_account"] is None
