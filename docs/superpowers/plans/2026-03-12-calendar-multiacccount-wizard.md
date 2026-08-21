# Calendar Multi-Account Wizard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Setup Wizard Google Calendar page to support multi-account auth (work + personal), per-account calendar exclusion defaults, and a default output calendar — all managed through the GUI.

**Architecture:** New `CalendarPreferences` runtime loader in `src/fateforger/core/` reads `./secrets/calendar-preferences.json`. The wizard's expanded `/setup/google` page reads `tokens.json` (read-only volume mount) to display accounts, calls MCP tools to add/remove accounts and list calendars, and writes preferences to `./secrets/calendar-preferences.json`. The bot reads prefs via the loader at startup.

**Tech Stack:** FastAPI + Jinja2 (wizard), autogen_ext MCP tool invocation, Python dataclasses (prefs model), JSON file storage, Docker bind mount for secrets sharing.

---

## Chunk 1: Core data model + helper

### Task 1: CalendarPreferences runtime loader

**Files:**
- Create: `src/fateforger/core/calendar_preferences.py`
- Create: `tests/unit/test_calendar_preferences.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_calendar_preferences.py
import json
from pathlib import Path
import pytest
from fateforger.core.calendar_preferences import CalendarPreferences, CalendarAccountPrefs

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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/hugoevers/VScode-projects/admonish-1
poetry run pytest tests/unit/test_calendar_preferences.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Implement the loader**

```python
# src/fateforger/core/calendar_preferences.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


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
        accounts = {
            account_id: CalendarAccountPrefs.from_dict(v if isinstance(v, dict) else {})
            for account_id, v in (raw.get("accounts") or {}).items()
        }
        return cls(
            default_write_account=raw.get("default_write_account"),
            default_write_calendar=raw.get("default_write_calendar"),
            accounts=accounts,
        )

    def excluded_calendars_for(self, account_id: str) -> list[str]:
        """Return excluded calendar IDs/names for the given account."""
        acct = self.accounts.get(account_id)
        return acct.excluded_calendars if acct else []
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
poetry run pytest tests/unit/test_calendar_preferences.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/core/calendar_preferences.py tests/unit/test_calendar_preferences.py
git commit -m "feat(core): add CalendarPreferences loader for multi-account calendar config"
```

---

### Task 2: Wizard calendar_prefs helper

**Files:**
- Create: `src/fateforger/setup_wizard/calendar_prefs.py`
- Create: `tests/unit/test_wizard_calendar_prefs.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_wizard_calendar_prefs.py
import json
from pathlib import Path
from fateforger.setup_wizard.calendar_prefs import (
    read_accounts,
    read_prefs,
    write_prefs,
)

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
    """Legacy format (bare token object) should return a single 'default' account."""
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps(TOKENS_LEGACY))
    accounts = read_accounts(p)
    assert "default" in accounts


def test_read_accounts_missing_file(tmp_path: Path) -> None:
    accounts = read_accounts(tmp_path / "nonexistent.json")
    assert accounts == {}


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
            "work": {"default_calendar": "hugo@work.com", "excluded_calendars": ["Holidays"]},
        },
    }
    write_prefs(p, data)
    loaded = read_prefs(p)
    assert loaded["default_write_account"] == "work"
    assert loaded["accounts"]["work"]["excluded_calendars"] == ["Holidays"]
```

- [ ] **Step 2: Run to confirm fail**

```bash
poetry run pytest tests/unit/test_wizard_calendar_prefs.py -v 2>&1 | head -20
```

- [ ] **Step 3: Implement the helper**

```python
# src/fateforger/setup_wizard/calendar_prefs.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_accounts(tokens_path: Path) -> dict[str, dict[str, Any]]:
    """Read authenticated accounts from the MCP tokens.json file.

    Returns a dict keyed by account nickname. Returns {} if file is missing or invalid.
    Handles legacy single-account format (bare token object without per-account keys).
    """
    if not tokens_path.exists():
        return {}
    try:
        raw: Any = json.loads(tokens_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    # Detect legacy format: top-level keys are token fields, not account IDs.
    token_fields = {"access_token", "refresh_token", "expiry_date"}
    if token_fields.intersection(raw.keys()):
        return {"default": raw}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def read_prefs(prefs_path: Path) -> dict[str, Any]:
    """Load calendar-preferences.json; return a dict with defaults if missing/invalid."""
    defaults: dict[str, Any] = {
        "version": 1,
        "default_write_account": None,
        "default_write_calendar": None,
        "accounts": {},
    }
    if not prefs_path.exists():
        return defaults
    try:
        raw: Any = json.loads(prefs_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return defaults
        # Merge with defaults so missing keys use defaults.
        merged = {**defaults, **raw}
        merged["accounts"] = raw.get("accounts") or {}
        return merged
    except Exception:
        return defaults


def write_prefs(prefs_path: Path, data: dict[str, Any]) -> None:
    """Write calendar preferences to JSON file, creating parent dirs as needed."""
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
```

- [ ] **Step 4: Run tests to confirm pass**

```bash
poetry run pytest tests/unit/test_wizard_calendar_prefs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/setup_wizard/calendar_prefs.py tests/unit/test_wizard_calendar_prefs.py
git commit -m "feat(wizard): add calendar_prefs helper for tokens.json reading and prefs JSON r/w"
```

---

## Chunk 2: Wizard backend routes

### Task 3: New and updated routes in app.py

**Files:**
- Modify: `src/fateforger/setup_wizard/app.py`
- Modify: `src/fateforger/setup_wizard/checks.py`

The goal is to:
1. Update `setup_google_page` (GET `/setup/google`) to pass `accounts`, `calendars_by_account`, and `prefs` to the template.
2. Add `POST /setup/google/add-account` — calls MCP `manage-accounts` tool, renders page with `oauth_url`.
3. Add `POST /setup/google/remove-account` — calls MCP `manage-accounts` tool with action=remove, redirects back.
4. Add `POST /setup/google/preferences` — parses form, writes `calendar-preferences.json`, redirects back.

**New helpers needed in `checks.py`** (or a new shared internal module):
- `_call_mcp_tool(url, tool_name, params)` — finds and calls a specific MCP tool, returns raw result
- `_extract_text(result)` — extracts text string from MCP tool result (handles content list or plain string)

**Token and prefs paths:**
- `tokens_path = Path("/config/calendar-mcp-tokens/tokens.json")` (read-only volume)
- `prefs_path = secrets_dir / "calendar-preferences.json"`

**Calendar listing:** call MCP `list-calendars` tool with no account filter (returns all accounts' calendars). Extract `id`, `summary`, `primary`, `accountId` from each calendar entry.

- [ ] **Step 1: Add MCP helpers to checks.py**

Add these two helpers after `_probe_mcp_tools`:

```python
def _extract_text_from_result(result: Any) -> str | None:
    """Extract plain text from an MCP tool call result."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        # Standard MCP content list: {"content": [{"type": "text", "text": "..."}]}
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return item.get("text")
        # Direct text field
        if "text" in result:
            return result["text"]
        # Direct url field (manage-accounts returns this sometimes)
        if "url" in result:
            return result["url"]
    return None


async def call_mcp_tool(
    *,
    url: str,
    tool_name: str,
    params: dict[str, Any],
    timeout_s: float = 5.0,
) -> tuple[Any, str | None]:
    """Find a named tool on the MCP server and call it with params.

    Returns (result, error_string). result is None on error.
    """
    tools, _, err = await _probe_mcp_tools(
        name=tool_name, url=url, timeout_s=timeout_s, try_mcp_suffix=False
    )
    if err:
        return None, err
    tool = next((t for t in tools if getattr(t, "name", "") == tool_name), None)
    if tool is None:
        return None, f"tool '{tool_name}' not found on MCP server"
    try:
        from autogen_core import CancellationToken
        result = await tool.run_json(params, CancellationToken())
        return result, None
    except Exception as e:
        return None, _safe_error(e)
```

- [ ] **Step 2: Add `_calendar_mcp_url()` and `_tokens_path()` helpers to app.py**

Add after `_secrets_dir()`:

```python
def _calendar_mcp_url() -> str:
    """URL of the calendar MCP server (within Docker network)."""
    port = os.getenv("PORT", "3000")
    return (
        os.getenv("WIZARD_CALENDAR_MCP_URL")
        or os.getenv("MCP_CALENDAR_SERVER_URL")
        or f"http://calendar-mcp:{port}"
    )


def _tokens_path() -> Path:
    """Path to the calendar MCP tokens.json (read-only volume mount)."""
    return Path("/config/calendar-mcp-tokens/tokens.json")


def _prefs_path() -> Path:
    """Path to the calendar-preferences.json file."""
    return _secrets_dir() / "calendar-preferences.json"
```

- [ ] **Step 3: Update `setup_google_page` to load accounts, calendars, and prefs**

Replace the existing GET `/setup/google` handler with:

```python
@app.get(
    "/setup/google", response_class=HTMLResponse, dependencies=[Depends(_require_admin)]
)
async def setup_google_page(
    request: Request,
    oauth_url: str | None = None,
    pending_account_id: str | None = None,
    add_error: str | None = None,
) -> HTMLResponse:
    """Render the Google Calendar setup page."""
    secrets_dir = _secrets_dir()
    gcal_path = secrets_dir / "gcal-oauth.json"
    env = read_env_file(_env_path())
    checks = await run_all_checks()
    checks_by_name = {c.name: c for c in checks}
    integrations = _build_integrations(
        env=env, checks_by_name=checks_by_name, gcal_present=gcal_path.exists()
    )

    accounts = read_accounts(_tokens_path())
    prefs = read_prefs(_prefs_path())

    # Best-effort: fetch calendar list from MCP (empty dict if MCP is down/unauthed).
    calendars_by_account: dict[str, list[dict]] = {}
    if accounts:
        result, err = await call_mcp_tool(
            url=_calendar_mcp_url(),
            tool_name="list-calendars",
            params={},
            timeout_s=4.0,
        )
        if result is not None:
            calendars_by_account = _parse_calendars(result, accounts)

    return templates.TemplateResponse(
        request,
        "setup_google.html",
        {
            "gcal_present": gcal_path.exists(),
            "gcal_path": str(gcal_path),
            "integrations": integrations,
            "active_integration": "calendar-mcp",
            "accounts": accounts,
            "calendars_by_account": calendars_by_account,
            "prefs": prefs,
            "oauth_url": oauth_url,
            "pending_account_id": pending_account_id,
            "add_error": add_error,
        },
    )
```

Add the `_parse_calendars` helper (in app.py, near the other helpers):

```python
def _parse_calendars(
    result: Any, accounts: dict[str, dict]
) -> dict[str, list[dict[str, Any]]]:
    """Parse a list-calendars MCP tool result into per-account calendar lists.

    Returns {account_id: [{"id": ..., "summary": ..., "primary": ..., "accountId": ...}]}.
    """
    import json as _json

    # Normalise: result might be a list, a JSON string, or a content-list dict.
    calendars: list[Any] = []
    if isinstance(result, list):
        calendars = result
    elif isinstance(result, str):
        try:
            parsed = _json.loads(result)
            if isinstance(parsed, list):
                calendars = parsed
        except Exception:
            pass
    elif isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        parsed = _json.loads(item["text"])
                        if isinstance(parsed, list):
                            calendars = parsed
                            break
                    except Exception:
                        pass

    grouped: dict[str, list[dict[str, Any]]] = {acc: [] for acc in accounts}
    for cal in calendars:
        if not isinstance(cal, dict):
            continue
        account_id = cal.get("accountId") or cal.get("account") or "default"
        if account_id not in grouped:
            grouped[account_id] = []
        grouped[account_id].append(
            {
                "id": cal.get("id", ""),
                "summary": cal.get("summary", cal.get("id", "")),
                "primary": bool(cal.get("primary")),
                "accountId": account_id,
            }
        )
    return grouped
```

- [ ] **Step 4: Add `POST /setup/google/add-account` route**

```python
@app.post(
    "/setup/google/add-account",
    response_class=HTMLResponse,
    dependencies=[Depends(_require_admin)],
)
async def setup_google_add_account(
    request: Request, account_id: str = Form("")
) -> HTMLResponse:
    """Trigger MCP account auth; render page with OAuth URL."""
    account_id = account_id.strip().lower()
    if not account_id:
        return await setup_google_page(request, add_error="Account ID cannot be empty.")

    result, err = await call_mcp_tool(
        url=_calendar_mcp_url(),
        tool_name="manage-accounts",
        params={"action": "add", "account_id": account_id},
        timeout_s=8.0,
    )

    if err:
        return await setup_google_page(
            request, add_error=f"Could not contact Calendar MCP: {err}"
        )

    # Extract OAuth URL from result.
    oauth_url = _extract_oauth_url(result)
    if not oauth_url:
        return await setup_google_page(
            request,
            add_error=f"MCP did not return an OAuth URL. Raw: {str(result)[:200]}",
        )

    return await setup_google_page(
        request, oauth_url=oauth_url, pending_account_id=account_id
    )
```

Add helper:

```python
def _extract_oauth_url(result: Any) -> str | None:
    """Try to extract an OAuth URL from a manage-accounts tool result."""
    from .checks import _extract_text_from_result

    text = _extract_text_from_result(result)
    if text and text.startswith("http"):
        return text
    # Result might be a dict with a "url" key directly.
    if isinstance(result, dict):
        url = result.get("url") or result.get("authUrl") or result.get("oauth_url")
        if url:
            return str(url)
    # Result might be a JSON string containing a url.
    if isinstance(text, str):
        try:
            import json as _json
            parsed = _json.loads(text)
            if isinstance(parsed, dict):
                url = parsed.get("url") or parsed.get("authUrl")
                if url:
                    return str(url)
        except Exception:
            pass
    return None
```

- [ ] **Step 5: Add `POST /setup/google/remove-account` route**

```python
@app.post(
    "/setup/google/remove-account",
    dependencies=[Depends(_require_admin)],
)
async def setup_google_remove_account(
    request: Request, account_id: str = Form("")
) -> RedirectResponse:
    """Remove an authenticated Google account via MCP."""
    account_id = account_id.strip()
    if account_id:
        await call_mcp_tool(
            url=_calendar_mcp_url(),
            tool_name="manage-accounts",
            params={"action": "remove", "account_id": account_id},
            timeout_s=5.0,
        )
    return RedirectResponse(url="/setup/google", status_code=303)
```

- [ ] **Step 6: Add `POST /setup/google/preferences` route**

The form will POST with fields:
- `default_write_account` (select)
- `default_write_calendar` (select)
- `excluded__{account_id}__{calendar_id}` checkboxes (present when INCLUDED; absent when excluded)
- `known_calendars__{account_id}` (hidden field listing all calendar IDs for that account)

```python
@app.post(
    "/setup/google/preferences",
    dependencies=[Depends(_require_admin)],
)
async def setup_google_save_preferences(request: Request) -> RedirectResponse:
    """Parse and save calendar preferences to calendar-preferences.json."""
    form = await request.form()

    default_write_account = (form.get("default_write_account") or "").strip() or None
    default_write_calendar = (form.get("default_write_calendar") or "").strip() or None

    # Build per-account exclusion lists.
    # For each account, we track all known calendars (from hidden field),
    # then exclude any that are NOT checked in the form.
    accounts_prefs: dict[str, Any] = {}
    for key, value in form.multi_items():
        if key.startswith("known_calendars__"):
            account_id = key[len("known_calendars__"):]
            # value is a JSON-encoded list of {"id": ..., "summary": ...} dicts
            try:
                import json as _json
                known: list[dict] = _json.loads(str(value))
            except Exception:
                known = []
            included_ids: set[str] = {
                v for k, v in form.multi_items()
                if k == f"included__{account_id}"
            }
            excluded = [
                cal["id"] for cal in known
                if cal.get("id") and cal["id"] not in included_ids
            ]
            default_cal_for_account = (
                form.get(f"default_calendar__{account_id}") or ""
            ).strip() or None
            accounts_prefs[account_id] = {
                "default_calendar": default_cal_for_account,
                "excluded_calendars": excluded,
            }

    prefs_data: dict[str, Any] = {
        "version": 1,
        "default_write_account": default_write_account,
        "default_write_calendar": default_write_calendar,
        "accounts": accounts_prefs,
    }
    write_prefs(_prefs_path(), prefs_data)
    return RedirectResponse(url="/setup/google", status_code=303)
```

- [ ] **Step 7: Add imports to app.py**

At the top of `app.py`, add to the existing imports:
```python
from .calendar_prefs import read_accounts, read_prefs, write_prefs
from .checks import call_mcp_tool
```

- [ ] **Step 8: Update `check_calendar_mcp` in checks.py to include account count**

In `check_calendar_mcp`, after getting `tools`, add:

```python
# Read account count from tokens.json for richer status display.
tokens_path = Path("/config/calendar-mcp-tokens/tokens.json")
account_count = len(read_accounts(tokens_path))
```

And include it in `details`:
```python
details={
    "url": resolved,
    "tool_count": len(tools),
    "tool_names_sample": [n for n in tool_names if n][:8],
    "account_count": account_count,
},
```

Add import at top of checks.py:
```python
from pathlib import Path
from .calendar_prefs import read_accounts
```

- [ ] **Step 9: Commit**

```bash
git add src/fateforger/setup_wizard/app.py src/fateforger/setup_wizard/checks.py
git commit -m "feat(wizard): add multi-account calendar routes (add/remove/preferences)"
```

---

## Chunk 3: Template + Docker

### Task 4: Update setup_google.html

**Files:**
- Modify: `src/fateforger/setup_wizard/templates/setup_google.html`

The page is divided into four card sections:
1. **OAuth credentials** (existing, kept as-is)
2. **Accounts** — list authenticated accounts; add-account form; pending OAuth URL
3. **Calendar preferences** — per-account calendar checkboxes; default calendar selector
4. **Instructions**

- [ ] **Step 1: Write the new template**

```html
{% extends "base.html" %}
{% block content %}

{# ── 1. OAuth credentials ────────────────────────────────────────── #}
<div class="card">
  <h2>OAuth App Credentials</h2>
  <p class="muted">Upload the <code>gcp-oauth.keys.json</code> (Desktop app type) from Google Cloud Console. This is the OAuth <em>app</em> credential shared by all accounts.</p>

  {% if gcal_present %}
    <p class="pill ok" style="margin-bottom:10px">OAuth JSON present: <code>{{ gcal_path }}</code></p>
  {% else %}
    <p class="pill warn" style="margin-bottom:10px">Missing OAuth JSON: <code>{{ gcal_path }}</code></p>
  {% endif %}

  <form method="post" action="/setup/google" enctype="multipart/form-data">
    <label>Upload credentials JSON</label>
    <input name="gcal_oauth_json" type="file" accept="application/json" />
    <div class="actions">
      <button class="btn" type="submit">Upload</button>
    </div>
  </form>

  <div class="hr"></div>
  <p class="muted">
    <a href="https://github.com/nspady/google-calendar-mcp" target="_blank" rel="noreferrer">google-calendar-mcp</a>
    •
    <a href="https://github.com/nspady/google-calendar-mcp/blob/main/docs/authentication.md" target="_blank" rel="noreferrer">Auth docs</a>
  </p>
</div>

{# ── 2. Accounts ──────────────────────────────────────────────────── #}
<div class="card" style="margin-top:14px">
  <h2>Google Accounts</h2>
  <p class="muted">Add one account per Google identity (e.g. <code>work</code>, <code>personal</code>). Each account authenticates independently.</p>

  {% if accounts %}
    <div style="margin-top:10px">
      {% for account_id, info in accounts.items() %}
      <div class="row" style="justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--border)">
        <div>
          <span style="font-size:13px; font-weight:600">{{ account_id }}</span>
          {% if info.get("cached_email") %}
            <span class="muted" style="margin-left:8px">{{ info.cached_email }}</span>
          {% endif %}
        </div>
        <form method="post" action="/setup/google/remove-account" style="display:inline">
          <input type="hidden" name="account_id" value="{{ account_id }}" />
          <button class="btn" type="submit" style="color:var(--bad); font-size:12px; padding:6px 10px">Remove</button>
        </form>
      </div>
      {% endfor %}
    </div>
  {% else %}
    <p class="pill warn" style="margin:10px 0">No accounts authenticated yet.</p>
  {% endif %}

  {% if add_error %}
    <p class="pill bad" style="margin-top:10px">{{ add_error }}</p>
  {% endif %}

  <div class="hr"></div>
  <form method="post" action="/setup/google/add-account">
    <label>Account nickname (e.g. <code>work</code> or <code>personal</code>)</label>
    <input name="account_id" type="text" placeholder="work" pattern="[a-z0-9_-]{1,64}" />
    <div class="actions">
      <button class="btn" type="submit">Add account</button>
    </div>
  </form>

  {% if oauth_url %}
  <div class="hr"></div>
  <div style="background:rgba(45,212,191,0.06); border:1px solid rgba(45,212,191,0.3); border-radius:12px; padding:14px; margin-top:10px">
    <p style="margin:0 0 8px; font-size:13px; font-weight:600; color:var(--ok)">Authorize "{{ pending_account_id }}"</p>
    <p class="muted">Open this URL in your browser to grant calendar access, then return here and refresh.</p>
    <pre style="margin:8px 0; word-break:break-all">{{ oauth_url }}</pre>
    <div class="actions">
      <a class="btn" href="{{ oauth_url }}" target="_blank" rel="noreferrer">Open Authorization URL</a>
      <form method="post" action="/health/refresh" style="display:inline">
        <button class="btn" type="submit">Refresh</button>
      </form>
    </div>
  </div>
  {% endif %}
</div>

{# ── 3. Calendar preferences ─────────────────────────────────────── #}
<div class="card" style="margin-top:14px">
  <h2>Calendar Preferences</h2>
  <p class="muted">Set which calendars the bot should query by default, and where it should write new events. Unchecked calendars are excluded from queries unless you explicitly ask about them.</p>

  {% if not accounts %}
    <p class="pill warn" style="margin-top:10px">Add at least one account above before configuring preferences.</p>
  {% else %}
  <form method="post" action="/setup/google/preferences">

    {# Default write account + calendar #}
    <div class="hr"></div>
    <label>Default output account</label>
    <select name="default_write_account" style="width:100%; background:rgba(11,16,32,0.75); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); font-size:13px">
      <option value="">— choose —</option>
      {% for account_id in accounts %}
        <option value="{{ account_id }}" {% if prefs.default_write_account == account_id %}selected{% endif %}>{{ account_id }}</option>
      {% endfor %}
    </select>

    <label>Default output calendar (calendar ID or primary email)</label>
    <select name="default_write_calendar" style="width:100%; background:rgba(11,16,32,0.75); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); font-size:13px">
      <option value="">— choose —</option>
      {% for account_id, cals in calendars_by_account.items() %}
        <optgroup label="{{ account_id }}">
          {% for cal in cals %}
            <option value="{{ cal.id }}" {% if prefs.default_write_calendar == cal.id %}selected{% endif %}>
              {{ cal.summary }}{% if cal.primary %} (primary){% endif %}
            </option>
          {% endfor %}
        </optgroup>
      {% endfor %}
    </select>

    {# Per-account calendar checkboxes #}
    {% for account_id, cals in calendars_by_account.items() %}
    <div class="hr"></div>
    <p style="font-size:13px; font-weight:600; margin:10px 0 6px">{{ account_id }} — calendars to include</p>
    <p class="muted" style="margin:0 0 8px">Unchecked calendars are excluded from default queries.</p>

    {% set acct_prefs = prefs.accounts.get(account_id, {}) %}
    {% set excluded = acct_prefs.get("excluded_calendars", []) %}

    {# Hidden field: list of all known calendars for this account (for server-side diffing) #}
    <input type="hidden" name="known_calendars__{{ account_id }}" value='{{ cals | tojson }}' />

    {# Default calendar for this account #}
    <label style="margin-top:10px">Default calendar for {{ account_id }}</label>
    <select name="default_calendar__{{ account_id }}" style="width:100%; background:rgba(11,16,32,0.75); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); font-size:13px">
      <option value="">— same as global default —</option>
      {% for cal in cals %}
        <option value="{{ cal.id }}" {% if acct_prefs.get("default_calendar") == cal.id %}selected{% endif %}>
          {{ cal.summary }}{% if cal.primary %} (primary){% endif %}
        </option>
      {% endfor %}
    </select>

    {% if cals %}
      <div style="margin-top:10px; display:flex; flex-direction:column; gap:6px">
        {% for cal in cals %}
        <label style="display:flex; align-items:center; gap:10px; margin:0; cursor:pointer">
          <input type="checkbox"
                 name="included__{{ account_id }}"
                 value="{{ cal.id }}"
                 {% if cal.id not in excluded %}checked{% endif %}
                 style="width:auto; cursor:pointer" />
          <span style="font-size:13px">
            {{ cal.summary }}
            {% if cal.primary %}<span class="muted">(primary)</span>{% endif %}
          </span>
        </label>
        {% endfor %}
      </div>
    {% else %}
      <p class="muted" style="font-size:12px">No calendars found for this account. Make sure the Calendar MCP is running.</p>
    {% endif %}
    {% endfor %}

    <div class="actions" style="margin-top:16px">
      <button class="btn" type="submit">Save preferences</button>
    </div>
  </form>
  {% endif %}

  {% if prefs.default_write_calendar %}
    <div class="hr"></div>
    <p class="muted">Current default output: <strong>{{ prefs.default_write_account or "—" }}</strong> › <code>{{ prefs.default_write_calendar }}</code></p>
  {% endif %}
</div>

{# ── 4. Instructions ─────────────────────────────────────────────── #}
<div class="card" style="margin-top:14px">
  <h2>Stack instructions</h2>
  <p class="muted">After adding accounts or uploading a new OAuth JSON, restart the calendar-mcp container:</p>
  <pre>docker compose up -d --build calendar-mcp</pre>
  <p class="muted" style="margin-top:10px">After saving preferences, restart the bot to pick up the new defaults:</p>
  <pre>docker compose restart slack-bot</pre>
</div>

{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add src/fateforger/setup_wizard/templates/setup_google.html
git commit -m "feat(wizard): expand Google Calendar page — accounts, calendar picker, preferences form"
```

---

### Task 5: docker-compose.yml — expose secrets to bot

**Files:**
- Modify: `docker-compose.yml`

Add a read-only bind mount of `./secrets` to the `slack-bot` service so the bot can read `calendar-preferences.json` (and any future secrets added there). The volume is read-only so the bot cannot modify credentials.

- [ ] **Step 1: Add bind mount to slack-bot**

In the `slack-bot` service `volumes` block, add:
```yaml
- ./secrets:/app/secrets:ro
```

- [ ] **Step 2: Verify the mount makes sense**

The bot's `CalendarPreferences.load()` reads from `/app/secrets/calendar-preferences.json` by default. If the file doesn't exist yet (first run before wizard is used), `load()` returns safe defaults — no crash.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "chore(docker): mount ./secrets read-only into slack-bot for calendar-preferences.json"
```

---

## Chunk 4: Integration + Verification

### Task 6: Run the full test suite

- [ ] **Step 1: Run all unit tests**

```bash
poetry run pytest tests/unit/ -v 2>&1 | tail -30
```

Expected: all existing tests still pass, new calendar tests pass.

- [ ] **Step 2: Run mypy / type check on new files (optional)**

```bash
poetry run mypy src/fateforger/core/calendar_preferences.py src/fateforger/setup_wizard/calendar_prefs.py --ignore-missing-imports 2>&1
```

- [ ] **Step 3: Manual smoke test checklist**

1. Start wizard: `docker compose up setup-wizard -d`
2. Open `http://localhost:8080` → login
3. Navigate to Google Calendar page
4. Verify: OAuth JSON status shows, accounts list is empty with "No accounts" message
5. Enter `work` in add-account field, submit → verify OAuth URL displayed
6. (Optional) complete OAuth flow in browser, refresh → account appears in list
7. Save preferences with defaults → verify `./secrets/calendar-preferences.json` created
8. Check JSON content matches expected schema
9. Verify dashboard health check shows account count

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore(issue-110): final cleanup and verification for calendar multi-account wizard"
```
