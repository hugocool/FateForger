from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CalendarEntry:
    """One calendar source or write target, identified by account name + calendar ID."""

    account: str
    calendar_id: str

    @classmethod
    def from_dict(cls, data: dict) -> "CalendarEntry":
        return cls(account=data["account"], calendar_id=data["calendar_id"])


@dataclass
class CalendarList:
    """A named set of calendar sources plus a designated write target."""

    calendars: list[CalendarEntry] = field(default_factory=list)
    write_calendar: CalendarEntry = field(
        default_factory=lambda: CalendarEntry(account="", calendar_id="")
    )


@dataclass
class CalendarAccountPrefs:
    """Per-account calendar preferences."""

    default_calendar: str | None = None
    excluded_calendars: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "CalendarAccountPrefs":
        """Build from a raw dict, ignoring unknown keys."""
        return cls(
            default_calendar=data.get("default_calendar"),
            excluded_calendars=list(data.get("excluded_calendars") or []),
        )


@dataclass
class CalendarPreferences:
    """Top-level calendar preferences loaded from calendar-preferences.json."""

    default_write_account: str | None = None
    default_write_calendar: str | None = None
    accounts: dict[str, CalendarAccountPrefs] = field(default_factory=dict)
    lists: dict[str, CalendarList] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "CalendarPreferences":
        """Load preferences from path. Returns defaults if file is missing or invalid."""
        if path is None:
            path = Path("/app/secrets/calendar-preferences.json")
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        if not isinstance(raw, dict):
            return cls()
        accounts = {
            account_id: CalendarAccountPrefs.from_dict(
                v if isinstance(v, dict) else {}
            )
            for account_id, v in (raw.get("accounts") or {}).items()
        }
        lists: dict[str, CalendarList] = {}
        for list_name, list_data in (raw.get("lists") or {}).items():
            if not isinstance(list_data, dict):
                continue
            calendars = [
                CalendarEntry.from_dict(c)
                for c in (list_data.get("calendars") or [])
                if isinstance(c, dict) and "account" in c and "calendar_id" in c
            ]
            raw_wc = list_data.get("write_calendar") or {}
            write_calendar = CalendarEntry(
                account=raw_wc.get("account", ""),
                calendar_id=raw_wc.get("calendar_id", ""),
            )
            lists[list_name] = CalendarList(calendars=calendars, write_calendar=write_calendar)
        return cls(
            default_write_account=raw.get("default_write_account"),
            default_write_calendar=raw.get("default_write_calendar"),
            accounts=accounts,
            lists=lists,
        )

    def excluded_calendars_for(self, account_id: str) -> list[str]:
        """Return excluded calendar IDs/names for the given account."""
        acct = self.accounts.get(account_id)
        return acct.excluded_calendars if acct else []
