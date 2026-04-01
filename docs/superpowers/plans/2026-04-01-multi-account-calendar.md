# Multi-Account Calendar Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend GCal integration to load and write events across multiple Google accounts, driven by a saved `lists["default"]` config that the setup wizard writes.

**Architecture:** Add `CalendarEntry`/`CalendarList` dataclasses to the data model, wire a single batched `load_list()` call into `McpCalendarClient` (the MCP's `calendarId` accepts a JSON array string), and add a wizard panel that writes `lists["default"]` to `calendar-preferences.json`.

**Tech Stack:** Python dataclasses, FastAPI (wizard), Jinja2 templates, pytest, existing `McpCalendarClient` / `read_prefs` / `write_prefs` helpers.

---

## Chunk 1: Data model — CalendarEntry, CalendarList, lists field

**Spec ref:** Changes 1 + `read_prefs` update  
**Files:**
- Modify: `src/fateforger/core/calendar_preferences.py`
- Modify: `src/fateforger/setup_wizard/calendar_prefs.py`
- Modify: `tests/unit/test_calendar_preferences.py`
- Modify: `tests/unit/test_wizard_calendar_prefs.py`

---

### Task 1: Add CalendarEntry, CalendarList, lists field to CalendarPreferences

**Files:**
- Modify: `src/fateforger/core/calendar_preferences.py`
- Modify: `tests/unit/test_calendar_preferences.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_calendar_preferences.py`:

```python
from fateforger.core.calendar_preferences import (
    CalendarAccountPrefs,
    CalendarEntry,
    CalendarList,
    CalendarPreferences,
)


def test_load_with_lists(tmp_path: Path) -> None:
    data = {
        "default_write_account": "biolytics",
        "default_write_calendar": "primary",
        "accounts": {},
        "lists": {
            "default": {
                "calendars": [
                    {"account": "biolytics", "calendar_id": "primary"},
                    {"account": "gerimedica", "calendar_id": "primary"},
                ],
                "write_calendar": {"account": "biolytics", "calendar_id": "primary"},
            }
        },
    }
    p = tmp_path / "prefs.json"
    p.write_text(json.dumps(data))
    prefs = CalendarPreferences.load(p)
    assert "default" in prefs.lists
    cal_list = prefs.lists["default"]
    assert len(cal_list.calendars) == 2
    assert cal_list.calendars[0].account == "biolytics"
    assert cal_list.calendars[1].calendar_id == "primary"
    assert cal_list.write_calendar.account == "biolytics"


def test_load_without_lists_returns_empty(tmp_path: Path) -> None:
    data = {"default_write_account": "work", "default_write_calendar": "primary"}
    p = tmp_path / "prefs.json"
    p.write_text(json.dumps(data))
    prefs = CalendarPreferences.load(p)
    assert prefs.lists == {}


def test_load_lists_skips_malformed_entries(tmp_path: Path) -> None:
    data = {
        "lists": {
            "default": {
                "calendars": [
                    {"account": "ok", "calendar_id": "primary"},
                    "not-a-dict",
                    {"calendar_id": "missing-account"},
                ],
                "write_calendar": {"account": "ok", "calendar_id": "primary"},
            }
        }
    }
    p = tmp_path / "prefs.json"
    p.write_text(json.dumps(data))
    prefs = CalendarPreferences.load(p)
    assert len(prefs.lists["default"].calendars) == 1
    assert prefs.lists["default"].calendars[0].account == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_calendar_preferences.py -k "test_load_with_lists or test_load_without_lists or test_load_lists_skips" -v
```

Expected: FAIL with `ImportError: cannot import name 'CalendarEntry'`

- [ ] **Step 3: Implement the data model changes**

In `src/fateforger/core/calendar_preferences.py`, add after the imports:

```python
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
```

Add `lists` field to `CalendarPreferences`:

```python
@dataclass
class CalendarPreferences:
    default_write_account: str | None = None
    default_write_calendar: str | None = None
    accounts: dict[str, CalendarAccountPrefs] = field(default_factory=dict)
    lists: dict[str, CalendarList] = field(default_factory=dict)   # NEW
```

Update `CalendarPreferences.load()` — add lists deserialization before the `return cls(...)` call:

```python
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
```

And update the `return cls(...)`:

```python
return cls(
    default_write_account=raw.get("default_write_account"),
    default_write_calendar=raw.get("default_write_calendar"),
    accounts=accounts,
    lists=lists,
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_calendar_preferences.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/core/calendar_preferences.py tests/unit/test_calendar_preferences.py
git commit -m "feat(calendar): add CalendarEntry, CalendarList, lists field to CalendarPreferences"
```

---

### Task 2: Update read_prefs to include lists key

The wizard uses `read_prefs()` to load the file before writing — it must preserve `lists` round-trip.

**Files:**
- Modify: `src/fateforger/setup_wizard/calendar_prefs.py`
- Modify: `src/fateforger/setup_wizard/app.py` (preferences save handler)
- Modify: `tests/unit/test_wizard_calendar_prefs.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_wizard_calendar_prefs.py`:

```python
def test_read_prefs_includes_lists_default(tmp_path: Path) -> None:
    prefs = read_prefs(tmp_path / "nonexistent.json")
    assert "lists" in prefs
    assert prefs["lists"] == {}


def test_read_prefs_preserves_lists(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "default_write_account": "work",
        "default_write_calendar": "primary",
        "accounts": {},
        "lists": {
            "default": {
                "calendars": [{"account": "work", "calendar_id": "primary"}],
                "write_calendar": {"account": "work", "calendar_id": "primary"},
            }
        },
    }
    p = tmp_path / "prefs.json"
    p.write_text(json.dumps(data))
    prefs = read_prefs(p)
    assert "lists" in prefs
    assert "default" in prefs["lists"]
    assert prefs["lists"]["default"]["calendars"][0]["account"] == "work"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_wizard_calendar_prefs.py -k "test_read_prefs_includes_lists or test_read_prefs_preserves_lists" -v
```

Expected: FAIL — `lists` key absent from returned dict

- [ ] **Step 3: Update read_prefs**

In `src/fateforger/setup_wizard/calendar_prefs.py`, update `read_prefs()`:

```python
def read_prefs(prefs_path: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "version": 1,
        "default_write_account": None,
        "default_write_calendar": None,
        "accounts": {},
        "lists": {},
    }
    if not prefs_path.exists():
        return defaults
    try:
        raw: Any = json.loads(prefs_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return defaults
        merged = {**defaults, **raw}
        merged["accounts"] = raw.get("accounts") or {}
        merged["lists"] = raw.get("lists") or {}
        return merged
    except Exception:
        return defaults
```

- [ ] **Step 4: Verify setup_google_save_preferences before modifying**

Read `setup_google_save_preferences` in `src/fateforger/setup_wizard/app.py` (around line 740). Confirm it ends with a `write_prefs(...)` call that does NOT include a `lists` key. The fix is to load existing prefs first and pass `lists` through unchanged.

- [ ] **Step 5: Update setup_google_save_preferences to preserve lists**

The existing `setup_google_save_preferences` in `src/fateforger/setup_wizard/app.py` writes a new dict that omits `lists`. Fix it to preserve the existing `lists` value:

```python
# At the top of the handler, after parsing form data:
existing = read_prefs(_prefs_path())

write_prefs(
    _prefs_path(),
    {
        "version": 1,
        "default_write_account": default_write_account,
        "default_write_calendar": default_write_calendar,
        "accounts": accounts_prefs,
        "lists": existing.get("lists") or {},   # preserve lists unchanged
    },
)
```

- [ ] **Step 6: Run all wizard prefs tests**

```bash
pytest tests/unit/test_wizard_calendar_prefs.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/setup_wizard/calendar_prefs.py src/fateforger/setup_wizard/app.py tests/unit/test_wizard_calendar_prefs.py
git commit -m "feat(wizard): read_prefs includes lists key; preferences save preserves lists"
```

---

## Chunk 2: McpCalendarClient.load_list()

**Spec ref:** Change 2  
**Files:**
- Modify: `src/fateforger/agents/timeboxing/mcp_clients.py`
- Modify: `tests/unit/test_timeboxing_mcp_calendar_client.py`

---

### Task 3: Add load_list() to McpCalendarClient

`load_list()` collects all `calendar_id` values from a `CalendarList` and issues a single `list_day_snapshot()` call. When there are multiple calendars, it passes all IDs as a JSON array string (the MCP server's `list-events` tool accepts this). Results are deduplicated by event ID (first occurrence wins).

**Files:**
- Modify: `src/fateforger/agents/timeboxing/mcp_clients.py`
- Modify: `tests/unit/test_timeboxing_mcp_calendar_client.py`

- [ ] **Step 1: Write failing tests**

The existing test file uses `McpCalendarClient.__new__(McpCalendarClient)` with a `_FakeWorkbench` — follow that pattern exactly. `_FakeWorkbench.last_arguments` captures the args passed to the tool call.

Add to `tests/unit/test_timeboxing_mcp_calendar_client.py`:

```python
# Add these imports at the top (alongside existing imports):
# from fateforger.core.calendar_preferences import CalendarEntry, CalendarList
# (json and date/ZoneInfo are already imported)


@pytest.mark.asyncio
async def test_load_list_empty_returns_empty_snapshot() -> None:
    """load_list with no calendars should return an empty snapshot without calling the workbench."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    workbench = _FakeWorkbench(payload={"events": [], "totalCount": 0})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[],
        write_calendar=CalendarEntry(account="work", calendar_id="primary"),
    )
    snapshot = await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    assert snapshot.response.events == []
    assert snapshot.immovables == []
    assert workbench.last_arguments is None  # workbench was never called


@pytest.mark.asyncio
async def test_load_list_single_calendar_passes_id_as_string() -> None:
    """Single calendar should pass calendarId as a plain string, not a JSON array."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    workbench = _FakeWorkbench(payload={"events": [], "totalCount": 0})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[CalendarEntry(account="work", calendar_id="work@gmail.com")],
        write_calendar=CalendarEntry(account="work", calendar_id="work@gmail.com"),
    )
    await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    assert workbench.last_arguments is not None
    assert workbench.last_arguments["calendarId"] == "work@gmail.com"


@pytest.mark.asyncio
async def test_load_list_multiple_calendars_passes_json_array() -> None:
    """Multiple calendars should pass calendarId as a JSON-encoded array string."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    workbench = _FakeWorkbench(payload={"events": [], "totalCount": 0})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[
            CalendarEntry(account="work", calendar_id="work@gmail.com"),
            CalendarEntry(account="personal", calendar_id="primary"),
        ],
        write_calendar=CalendarEntry(account="work", calendar_id="work@gmail.com"),
    )
    await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    assert workbench.last_arguments is not None
    ids = json.loads(workbench.last_arguments["calendarId"])
    assert ids == ["work@gmail.com", "primary"]


@pytest.mark.asyncio
async def test_load_list_deduplicates_by_event_id() -> None:
    """Events with duplicate IDs should be deduplicated; first occurrence wins."""
    from fateforger.core.calendar_preferences import CalendarEntry, CalendarList

    events = [
        {
            "id": "evt-1", "summary": "First copy",
            "start": {"dateTime": "2026-04-01T09:00:00"},
            "end": {"dateTime": "2026-04-01T10:00:00"},
        },
        {
            "id": "evt-1", "summary": "Duplicate",
            "start": {"dateTime": "2026-04-01T09:00:00"},
            "end": {"dateTime": "2026-04-01T10:00:00"},
        },
        {
            "id": "evt-2", "summary": "Unique",
            "start": {"dateTime": "2026-04-01T11:00:00"},
            "end": {"dateTime": "2026-04-01T12:00:00"},
        },
    ]
    workbench = _FakeWorkbench(payload={"events": events, "totalCount": len(events)})
    client = McpCalendarClient.__new__(McpCalendarClient)
    client._workbench = workbench
    list_def = CalendarList(
        calendars=[
            CalendarEntry(account="work", calendar_id="primary"),
            CalendarEntry(account="personal", calendar_id="other@gmail.com"),
        ],
        write_calendar=CalendarEntry(account="work", calendar_id="primary"),
    )
    snapshot = await client.load_list(
        list_def=list_def, day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam")
    )
    event_ids = [e.id for e in snapshot.response.events]
    assert event_ids.count("evt-1") == 1
    summaries = [e.summary for e in snapshot.response.events if e.id == "evt-1"]
    assert summaries == ["First copy"]
    assert len(snapshot.response.events) == 2  # evt-1 (deduplicated) + evt-2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_timeboxing_mcp_calendar_client.py -k "test_load_list" -v
```

Expected: FAIL with `AttributeError: 'McpCalendarClient' has no attribute 'load_list'`

- [ ] **Step 3: Implement load_list()**

Add to `McpCalendarClient` in `src/fateforger/agents/timeboxing/mcp_clients.py`, after `list_day_snapshot()`.

`GCalEventsResponse` is already imported at the top of the file (line 18) — use it directly, no lazy import needed.
`CalendarList` is used only as a type annotation — since the file has `from __future__ import annotations`, no runtime import is needed.

```python
async def load_list(
    self,
    *,
    list_def: "CalendarList",
    day: date,
    tz: ZoneInfo,
) -> CalendarDaySnapshot:
    """Fetch a day snapshot for all calendars in a CalendarList.

    Passes all calendar IDs in a single list-events call. When multiple
    calendars are requested, calendarId is a JSON array string as accepted
    by the GCal MCP server. Results are deduplicated by event ID (first
    occurrence wins).
    """
    calendar_ids = [entry.calendar_id for entry in list_def.calendars]
    if not calendar_ids:
        return CalendarDaySnapshot(
            response=GCalEventsResponse(events=[], totalCount=0),
            immovables=[],
        )

    calendar_id_arg = calendar_ids[0] if len(calendar_ids) == 1 else json.dumps(calendar_ids)
    snapshot = await self.list_day_snapshot(calendar_id=calendar_id_arg, day=day, tz=tz)

    # Deduplicate by event ID — first occurrence wins.
    # GCalEvent.id is a required str field, never None.
    seen: set[str] = set()
    unique_events = []
    for event in snapshot.response.events:
        if event.id not in seen:
            seen.add(event.id)
            unique_events.append(event)

    if len(unique_events) == len(snapshot.response.events):
        return snapshot  # no duplicates — return as-is

    deduped_response = GCalEventsResponse(
        events=unique_events, totalCount=len(unique_events)
    )
    immovables = self._immovables_from_response(
        response=deduped_response, day=day, tz=tz
    )
    return CalendarDaySnapshot(response=deduped_response, immovables=immovables)
```

> **Add the type annotation import:** `CalendarList` is used in the signature as a string annotation (safe with `from __future__ import annotations`). For IDE support, add to the `TYPE_CHECKING` block at the top of `mcp_clients.py`:
>
> ```python
> if TYPE_CHECKING:
>     from fateforger.core.calendar_preferences import CalendarList
> ```
>
> Check whether a `TYPE_CHECKING` block already exists; if not, add `from typing import TYPE_CHECKING` to imports and then the block.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_timeboxing_mcp_calendar_client.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/mcp_clients.py tests/unit/test_timeboxing_mcp_calendar_client.py
git commit -m "feat(mcp-client): add load_list() for multi-calendar batched fetch with dedup"
```

---

## Chunk 3: Wizard UI — Calendar Lists panel

**Spec ref:** Change 3  
**Files:**
- Modify: `src/fateforger/setup_wizard/app.py`
- Modify: `src/fateforger/setup_wizard/templates/setup_google.html`

No unit tests for the wizard route (existing wizard routes are untested at the route level; follow that pattern). Manual verification steps are listed instead.

---

### Task 4: Add /setup/google/calendar-list POST route + wizard panel

**Files:**
- Modify: `src/fateforger/setup_wizard/app.py`
- Modify: `src/fateforger/setup_wizard/templates/setup_google.html`

- [ ] **Step 1: Add the POST route to app.py**

Add after the `setup_google_save_preferences` handler (around line 780 in `app.py`):

```python
@app.post(
    "/setup/google/calendar-list",
    dependencies=[Depends(_require_admin)],
)
async def setup_google_save_calendar_list(request: Request) -> RedirectResponse:
    """Save the default calendar load list to calendar-preferences.json."""
    form = await request.form()

    # Collect selected calendar entries: form fields named "list_cal__<account>__<cal_id>"
    calendars: list[dict[str, str]] = []
    for key, _value in form.multi_items():
        if not key.startswith("list_cal__"):
            continue
        parts = key[len("list_cal__"):].split("__", 1)
        if len(parts) != 2:
            continue
        account, calendar_id = parts
        calendars.append({"account": account, "calendar_id": calendar_id})

    write_account = (form.get("list_write_account") or "").strip()
    write_calendar_id = (form.get("list_write_calendar_id") or "").strip()

    list_def: dict[str, Any] = {
        "calendars": calendars,
        "write_calendar": {"account": write_account, "calendar_id": write_calendar_id},
    }

    existing = read_prefs(_prefs_path())
    existing_lists = existing.get("lists") or {}
    existing_lists["default"] = list_def

    write_prefs(
        _prefs_path(),
        {
            **existing,
            "lists": existing_lists,
        },
    )
    return RedirectResponse(url="/setup/google", status_code=303)
```

- [ ] **Step 2: Add the calendar list panel to setup_google.html**

Add a new card section at the end of `setup_google.html`, before the `{% endblock %}` / Stack instructions block:

```html
{# ── 4. Default Calendar List ────────────────────────────────────── #}
<div class="card" style="margin-top:14px">
  <h2>Default Calendar Load List</h2>
  <p class="muted">
    Choose which calendars to load when planning your day. All checked calendars
    are fetched together in one request. The <strong>write target</strong> is where
    patched or new events are saved.
  </p>

  {% if not accounts %}
    <p class="pill warn" style="margin-top:10px">Add at least one account above before configuring the load list.</p>
  {% else %}
  <form method="post" action="/setup/google/calendar-list">

    {% set current_list = prefs.lists.get("default", {}) if prefs.lists else {} %}
    {% set current_cal_ids = [] %}
    {% for entry in (current_list.get("calendars") or []) %}
      {% set _ = current_cal_ids.append(entry.get("calendar_id")) %}
    {% endfor %}

    {% set current_wc = current_list.get("write_calendar", {}) %}
    {% set current_write_id = current_wc.get("calendar_id", "") if current_wc else "" %}
    {% set current_write_account = current_wc.get("account", "") if current_wc else "" %}

    {% for account_id, cals in calendars_by_account.items() %}
    <div class="hr"></div>
    <p style="font-size:13px; font-weight:600; margin:12px 0 6px">{{ account_id }}</p>
    {% for cal in cals %}
    <label style="display:flex; align-items:center; gap:10px; margin:0 0 6px; cursor:pointer">
      <input type="checkbox"
             name="list_cal__{{ account_id }}__{{ cal.id }}"
             value="{{ cal.id }}"
             {% if cal.id in current_cal_ids or not current_cal_ids %}checked{% endif %}
             style="width:auto; cursor:pointer; accent-color:var(--ok)" />
      <span style="font-size:13px">{{ cal.summary }}{% if cal.primary %} <span class="muted">(primary)</span>{% endif %}</span>
    </label>
    {% endfor %}
    {% endfor %}

    <div class="hr"></div>
    <label style="margin-top:12px">Write target account</label>
    <select name="list_write_account" style="width:100%; background:rgba(11,16,32,0.75); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); font-size:13px; box-sizing:border-box">
      <option value="">— choose —</option>
      {% for account_id in accounts %}
        <option value="{{ account_id }}" {% if current_write_account == account_id %}selected{% endif %}>{{ account_id }}</option>
      {% endfor %}
    </select>

    <label style="margin-top:12px">Write target calendar</label>
    <select name="list_write_calendar_id" style="width:100%; background:rgba(11,16,32,0.75); border:1px solid var(--border); border-radius:10px; padding:10px 12px; color:var(--text); font-size:13px; box-sizing:border-box">
      <option value="">— choose —</option>
      {% for account_id, cals in calendars_by_account.items() %}
        {% if cals %}
        <optgroup label="{{ account_id }}">
          {% for cal in cals %}
            <option value="{{ cal.id }}" {% if current_write_id == cal.id %}selected{% endif %}>
              {{ cal.summary }}{% if cal.primary %} (primary){% endif %}
            </option>
          {% endfor %}
        </optgroup>
        {% endif %}
      {% endfor %}
    </select>

    <div class="actions" style="margin-top:18px">
      <button class="btn" type="submit" style="color:var(--ok); border-color:rgba(45,212,191,0.4)">Save load list</button>
    </div>
  </form>

  {% if current_list %}
  <div class="hr"></div>
  <p class="muted">Current list: {{ (current_list.get("calendars") or []) | length }} calendar(s) → write to <code>{{ current_write_id or "—" }}</code></p>
  {% endif %}
  {% endif %}
</div>
```

> **Note on `prefs.lists`:** The template context passes `prefs` as the raw dict returned by `read_prefs()`. With Task 2 complete, `prefs["lists"]` is always present. The Jinja2 dict access `.get("lists", {})` is used defensively.

- [ ] **Step 3: Verify setup_google_page passes lists in prefs context**

Check `setup_google_page` in `app.py` — it calls `read_prefs(_prefs_path())` and passes `prefs` to the template. With Task 2 done, `prefs` will include `lists`. No change needed unless `prefs` is passed as a `CalendarPreferences` object rather than a raw dict — verify and adapt if needed.

- [ ] **Step 4: Manual smoke test**

```bash
# Start the wizard
cd /path/to/project && python -m fateforger.setup_wizard.app
# Navigate to http://localhost:8000/setup/google
# Verify the "Default Calendar Load List" card appears
# Check boxes and save
# Verify calendar-preferences.json contains lists.default
```

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/setup_wizard/app.py src/fateforger/setup_wizard/templates/setup_google.html
git commit -m "feat(wizard): add calendar load list panel — saves lists.default to calendar-preferences.json"
```

---

## Chunk 4: CLI integration (depends on PR #119)

> **Prerequisite:** This chunk requires PR #119 (`cli/patch.py` + `PatchSession`) to be merged into `main` and this branch rebased onto it (or merged from it).

**Spec ref:** Change 4  
**Files:**
- Modify: `src/fateforger/cli/patch.py` (from PR #119)
- Modify: `tests/` (integration or unit test for `_load_from_gcal`)

---

### Task 5: Use load_list() in PatchSession._load_from_gcal

- [ ] **Step 1: Confirm PR #119 is merged and available on this branch**

```bash
git log --oneline main | head -5
# Verify cli/patch.py exists
ls src/fateforger/cli/patch.py
```

- [ ] **Step 2: Write failing test**

Add a test (location: `tests/unit/test_patch_session_load.py` or wherever `PatchSession` tests live):

```python
@pytest.mark.asyncio
async def test_load_from_gcal_uses_load_list_when_default_list_present(
    mock_client, mock_prefs_with_list
) -> None:
    """PatchSession should call load_list() when lists["default"] is configured."""
    session = PatchSession(client=mock_client, prefs=mock_prefs_with_list, ...)
    await session._load_from_gcal(day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam"))
    mock_client.load_list.assert_called_once()
    mock_client.list_day_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_load_from_gcal_falls_back_to_single_account_without_list(
    mock_client, mock_prefs_no_list
) -> None:
    """PatchSession falls back to list_day_snapshot() when lists is empty."""
    session = PatchSession(client=mock_client, prefs=mock_prefs_no_list, ...)
    await session._load_from_gcal(day=date(2026, 4, 1), tz=ZoneInfo("Europe/Amsterdam"))
    mock_client.list_day_snapshot.assert_called_once()
    mock_client.load_list.assert_not_called()
```

- [ ] **Step 3: Update PatchSession._load_from_gcal**

In `src/fateforger/cli/patch.py`, update `_load_from_gcal`:

```python
async def _load_from_gcal(self, *, day: date, tz: ZoneInfo) -> CalendarDaySnapshot:
    prefs = CalendarPreferences.load(self._prefs_path)
    if "default" in prefs.lists:
        return await self._client.load_list(
            list_def=prefs.lists["default"], day=day, tz=tz
        )
    # Fallback: single-account behaviour
    calendar_id = (
        self._calendar_id
        or (prefs.default_write_calendar)
        or "primary"
    )
    return await self._client.list_day_snapshot(
        calendar_id=calendar_id, day=day, tz=tz
    )
```

Add `--list NAME` flag to the `tmbx` entry point (in `cli/patch.py` argument parsing) to select a non-default list:

```python
# In the argparse / CLI setup:
parser.add_argument(
    "--list",
    metavar="NAME",
    default="default",
    help="Name of the calendar list to load (default: 'default')",
)
```

And update `_load_from_gcal` to use `list_name` parameter:

```python
async def _load_from_gcal(self, *, day: date, tz: ZoneInfo, list_name: str = "default") -> CalendarDaySnapshot:
    prefs = CalendarPreferences.load(self._prefs_path)
    if list_name in prefs.lists:
        return await self._client.load_list(
            list_def=prefs.lists[list_name], day=day, tz=tz
        )
    # Fallback
    ...
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/ -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/cli/patch.py tests/
git commit -m "feat(tmbx): use load_list() from lists[default] when present; add --list flag"
```
