# Multi-Account Calendar Support — Design

**Date:** 2026-04-01
**Issue:** hugocool/FateForger#120 (flexible planning window, related)
**Branch:** issue/110-calendar-multiacccount-wizard

## Problem

The codebase hard-codes a single calendar account and single calendar ID. Users may have multiple Google accounts (e.g. `biolytics`, `gerimedica`) each with calendars they want loaded and planned together. There is no way to configure which calendars to include or which to write patched events back to.

## Scope

- GCal only for now; same pattern applies to Notion/TickTick later
- Minimum viable: extend existing types + one wizard panel + one new client method
- No re-implementation of auth (the GCal MCP server owns that via its own UI at port 3500)
- No account management UI in the wizard beyond a link to the MCP's auth page

## Architecture

```
[GCal MCP server]          owns auth tokens, account names (tokens.json)
       ↑
[McpCalendarClient]        reads prefs, fetches calendars in one batched call
       ↑                   ↑
[tmbx CLI]         [Timeboxing agent]   — both consume the same client + prefs
(PR #119, not yet merged)

[Wizard /setup/google]     reads accounts from MCP, writes calendar-preferences.json
```

**Constraint:** Account names in `calendar-preferences.json` must match keys in the MCP's `tokens.json`. The wizard enforces this by only offering names it read from the MCP — never free-text entry.

## Data Model

### New types (extend `core/calendar_preferences.py`)

```python
@dataclass
class CalendarEntry:
    account: str        # matches a key in tokens.json (e.g. "biolytics") — metadata only
    calendar_id: str    # GCal calendar ID ("primary", email address, etc.)

@dataclass
class CalendarList:
    calendars: list[CalendarEntry]          # sources to load
    write_calendar: CalendarEntry           # where patched/new events go
```

`account` on `CalendarEntry` is stored for display/audit purposes. The MCP `list-events` call does not accept an `accountId` parameter — it routes by `calendarId` alone (the MCP server resolves which account owns a given calendar ID from its `tokens.json`).

### Extended `CalendarPreferences`

```python
@dataclass
class CalendarPreferences:
    default_write_account: str | None = None    # kept for backward compat
    default_write_calendar: str | None = None   # kept for backward compat
    accounts: dict[str, CalendarAccountPrefs] = field(default_factory=dict)
    lists: dict[str, CalendarList] = field(default_factory=dict)  # NEW
```

`lists["default"]` is used when nothing is specified. If absent, falls back to existing `default_write_account` / `default_write_calendar` single-account behaviour.

### `calendar-preferences.json` on disk

```json
{
  "version": 1,
  "default_write_account": "biolytics",
  "default_write_calendar": "primary",
  "accounts": {},
  "lists": {
    "default": {
      "calendars": [
        {"account": "biolytics", "calendar_id": "primary"},
        {"account": "gerimedica", "calendar_id": "primary"}
      ],
      "write_calendar": {"account": "biolytics", "calendar_id": "primary"}
    }
  }
}
```

## Changes

### 1. `core/calendar_preferences.py`

- Add `CalendarEntry` and `CalendarList` dataclasses
- Add `lists` field to `CalendarPreferences`
- Update `CalendarPreferences.load()` to deserialise `lists`
- Update `read_prefs()` to default `lists` to `{}` when key absent (backward compat)

### 2. `agents/timeboxing/mcp_clients.py` — `McpCalendarClient`

New method:

```python
async def load_list(
    self,
    *,
    list_def: CalendarList,
    day: date,
    tz: ZoneInfo,
) -> CalendarDaySnapshot:
```

**Implementation:** The GCal MCP `list-events` tool accepts `calendarId` as either a single string or a JSON-encoded array string (e.g. `'["primary", "someone@gmail.com"]'`). `load_list()` collects all `entry.calendar_id` values from `list_def.calendars`, serialises them to a JSON array string, and issues a **single** `list_day_snapshot()` call with that array.

**Deduplication:** If the same event ID appears in results from multiple calendar IDs (e.g. a shared event), the first occurrence wins. No merging of event fields across duplicates.

Existing `list_day_snapshot()` is unchanged.

### 3. `setup_wizard/app.py` + `/setup/google` template

Additions to the existing Google Calendar setup page:

1. **"Add account" button** — opens `http://localhost:3500` (the MCP's own auth UI) in a new tab. No re-implementation.
2. **Calendar list panel** — calls `list-calendars` on the GCal MCP to enumerate available calendars per registered account. Shows checkboxes: which calendars to include in the default load list, which is the write target.
3. **On save:** reads existing `calendar-preferences.json` via `read_prefs()`, updates only the `lists["default"]` key, and writes back via `write_prefs()`. All other keys (`default_write_account`, `default_write_calendar`, `accounts`) are preserved.

### 4. `cli/patch.py` — `PatchSession._load_from_gcal` (PR #119)

> Note: `cli/patch.py` and `PatchSession` are implemented in PR #119 (not yet merged to main).

- Reads `CalendarPreferences` from the configured path
- If `lists["default"]` exists, calls `client.load_list(list_def=prefs.lists["default"], ...)`
- Otherwise falls back to existing single-account `list_day_snapshot()` behaviour
- `tmbx --list NAME` selects a non-default list (plumbed through to `_load_from_gcal`)

## What is NOT in scope

- CRUD commands for lists in the CLI — the wizard owns list config
- Cross-calendar event routing on submit (future issue)
- Notion / TickTick multi-account (same pattern, separate issues)
- Renaming or aliasing MCP account names — names are owned by the MCP auth flow

## Backward compatibility

All changes are additive. If `lists` is absent from `calendar-preferences.json`, existing single-account behaviour is preserved. Existing callers of `list_day_snapshot()` are unaffected.
