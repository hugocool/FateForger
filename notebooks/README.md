# Notebooks

## Status

- **Documented** (2025-07-22): Phase 5 integration test notebook added (sync engine + patching live tests).
- **Documented** (2026-01-26): Calendar MCP tool discovery + `fields` allowlist probing notes added.
- **Documented** (2026-02-13): Notebook workflow now uses `WIP/` (active issue work) and `DONE/` (DoD-complete notebooks).
- **Implemented** (2026-02-13): Notebook git hygiene/checkpoint automation added (`.gitattributes`, PR template sections, notebook CI policy checks).

## Purpose

This folder contains exploratory and development notebooks that should import application code from `src/`
without `sys.path` hacks. Ensure VS Code/Jupyter uses the Poetry virtualenv at `.venv/`.

## README vs AGENTS

- `README.md` (this file) is the human-readable index, context, and runbook.
- `AGENTS.md` files contain agent operating rules, lifecycle constraints, and extraction protocol.
- Keep these concerns separate: architecture/how-to here, agent behavior rules in `AGENTS.md`.

## Notebook lifecycle and folder model

- `notebooks/WIP/`: active issue notebooks (work in progress).
- `notebooks/DONE/`: completed notebooks where DoD is met and reruns are expected to pass.
- `notebooks/features/`: retained reference notebooks for feature/design explanation.
- Root-level notebooks may exist as legacy/reference material; migrate active work into `WIP/`.

## Git hygiene baseline (notebook workflow)

- Notebook git attributes are enforced via `.gitattributes`:
  - `filter=nbstripout`
  - `diff=jupyternotebook`
  - `merge=jupyternotebook`
- CI policy checks run via `.github/workflows/notebook-workflow-checks.yml` using `scripts/dev/notebook_workflow_checks.py`.
- Optional per-issue choice: pair notebooks with Jupytext text representation when it improves review quality.
- Local setup helpers (run once per machine):

```bash
poetry run nbstripout --install
poetry run nbdime config-git --enable
```

### Notebook DoD expectations

- Linked issue/PR acceptance criteria are satisfied.
- Durable logic extracted to `src/`, `tests/`, and docs where appropriate.
- Notebook metadata header is complete and current.
- Notebook reruns from a clean kernel without errors.

## Notebook Index

| Notebook | Purpose | Status |
|----------|---------|--------|
| `phase5_integration_test.ipynb` | Live MCP + LLM integration tests for sync engine, patching, submitter. 10 sections. | Passing (2025-07-22) |
| `making_timebox_session_stage_4_work.ipynb` | Stage 4 refine flow with JSON constraint patterns. Reference for patching approach. | Reference |
| `submit_timebox_to_cal.ipynb` | Calendar submission flow exploration. | Exploratory |
| `test_calendar_agent.ipynb` | Calendar agent MCP integration testing. | Exploratory |
| `test_constraint_extractor.ipynb` | Constraint extractor agent testing. | Exploratory |
| `test_timebox_storage.ipynb` | Timebox storage/persistence testing. | Exploratory |
| `caniconal_agent.ipynb` | Canonical agent pattern exploration. | Exploratory |
| `clean_working_mcp.ipynb` | Clean MCP client setup reference. | Reference |
| `minimal_working_mcp.ipynb` | Minimal MCP connectivity test. | Reference |
| `getting_it_working.ipynb` | General development scratchpad. | Exploratory |
| `notion_mcp.ipynb` | Notion MCP integration testing. | Exploratory |
| `openrouter_request_shape.ipynb` | OpenRouter API request shape exploration. | Exploratory |
| `patching_json.ipynb` | JSON patching exploration (pre-schema-in-prompt). | Archived |
| `ticktick_agent.ipynb` | TickTick agent MCP integration. | Exploratory |
| `todo_agent_ticktick.ipynb` | TickTick todo agent exploration. | Exploratory |
| `features/gradio_chat.ipynb` | Gradio chat integration reference notebook. | Reference |

### Subfolders

| Folder | Contents |
|--------|----------|
| `DONE/` | Completed notebooks with DoD met and clean rerun expectation. |
| `features/` | Feature-specific notebooks (ad hoc exploration). |
| `WIP/` | Work-in-progress notebooks linked to active GitHub issues/PRs. |

## Google Calendar MCP: tool args + allowed `fields`

This repo runs the Google Calendar MCP server via Docker Compose (see `docker-compose.yml`, service
`calendar-mcp`). The notebook should treat the **running MCP server’s tool schemas** as the source of
truth for what arguments are accepted.

### 1) Discover tools and their argument schema

```python
import json

from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

params = StreamableHttpServerParams(url=get_calendar_mcp_server_url(), timeout=10.0)
tools = await mcp_server_tools(params)

for t in tools:
    print(t.name)

list_events = next(t for t in tools if t.name == "list-events")
print(json.dumps(list_events.schema["parameters"], indent=2))
```

### 2) Find the allowlist for `list-events.fields`

The `list-events` tool accepts a `fields` argument (array of strings) and **validates each entry against
an allowlist**, but that allowlist is not necessarily present in `list_events.schema`.

You can reliably extract the allowlist by intentionally passing an invalid field and reading the MCP
error payload:

```python
import json

from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

workbench = McpWorkbench(StreamableHttpServerParams(url=get_calendar_mcp_server_url(), timeout=10.0))
r = await workbench.call_tool("list-events", arguments={"calendarId": "primary", "fields": ["__invalid__"]})

assert r.is_error
err_text = r.result[0].content  # TextResultContent
details = json.loads(err_text.split("list-events: ", 1)[1])  # list[dict]
allowed_fields = details[0]["options"]  # list[str]
allowed_fields
```

On 2026-01-26, the server returned this allowlist:

```
id, summary, description, start, end, location, attendees, colorId, transparency,
extendedProperties, reminders, conferenceData, attachments, status, htmlLink, created,
updated, creator, organizer, recurrence, recurringEventId, originalStartTime, visibility,
iCalUID, sequence, hangoutLink, anyoneCanAddSelf, guestsCanInviteOthers, guestsCanModify,
guestsCanSeeOtherGuests, privateCopy, locked, source, eventType
```

### 3) Pydantic parsing coverage (for later conversion to `CalendarEvent`)

If your Pydantic model uses `extra="allow"`, then *all* fields returned by the MCP server will be
accepted even if they’re not explicitly declared. If you want convenient typed access, explicitly model
the subset you plan to convert into your internal `CalendarEvent` (commonly: `description`, `location`,
`attendees`, and recurrence-related fields).

## `search-events`: tool args + allowed `fields`

`search-events` has the same `fields` mechanism as `list-events`, but it requires a text query and a
time window.

### 1) Discover `search-events` argument schema

```python
import json

from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

params = StreamableHttpServerParams(url=get_calendar_mcp_server_url(), timeout=10.0)
tools = await mcp_server_tools(params)

search_events = next(t for t in tools if t.name == "search-events")
print(json.dumps(search_events.schema["parameters"], indent=2))
```

On this repo’s current server, `search-events` requires:

- `calendarId` (string or list of strings)
- `query` (string)
- `timeMin` (string)
- `timeMax` (string)

and accepts the optional keys: `account`, `timeZone`, `fields`, `privateExtendedProperty`,
`sharedExtendedProperty`.

### 2) Get the allowlist for `search-events.fields`

Use the same invalid-field probe pattern as `list-events`:

```python
import json

r = await workbench.call_tool(
    "search-events",
    arguments={
        "calendarId": "primary",
        "query": "__probe__",
        "timeMin": "2026-01-27T00:00:00",
        "timeMax": "2026-01-28T00:00:00",
        "fields": ["__invalid__"],
    },
)

assert r.is_error
err_text = r.result[0].content
details = json.loads(err_text.split("search-events: ", 1)[1])
allowed_fields = details[0]["options"]
allowed_fields
```

## `create-event`: tool args (no probing)

`create-event` has side effects. Do **not** “probe” it by sending invalid data unless you also clean up
the created event. Prefer reading the schema from `mcp_server_tools(...)`.

### 1) Discover `create-event` argument schema

```python
import json

from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

params = StreamableHttpServerParams(url=get_calendar_mcp_server_url(), timeout=10.0)
tools = await mcp_server_tools(params)

create_event = next(t for t in tools if t.name == "create-event")
print(json.dumps(create_event.schema["parameters"], indent=2))
```

On this repo’s current server, `create-event` requires:

- `calendarId` (string)
- `summary` (string)
- `start` (string)
- `end` (string)

Notes:

- `start`/`end` are described as accepting either:
  - timezone-naive strings like `2026-01-27T09:00:00` (uses `timeZone` if provided), or
  - RFC3339 strings like `2026-01-27T09:00:00+01:00`.
  - The tool’s description also mentions Google’s object format (`{dateTime, timeZone}` / `{date}`),
    but the schema’s `type` is `string`, so prefer strings unless you’ve tested object payloads.
- Enums you’ll likely use:
  - `sendUpdates`: `all | externalOnly | none`
  - `visibility`: `default | public | private | confidential`
  - `transparency`: `opaque | transparent`
  - `eventType`: `default | focusTime | outOfOffice | workingLocation`

### 2) Minimal `create-event` example

```python
result = await workbench.call_tool(
    "create-event",
    arguments={
        "calendarId": "primary",
        "summary": "morning review",
        "start": "2026-01-27T09:00:00",
        "end": "2026-01-27T09:15:00",
        "timeZone": "Europe/Amsterdam",
        "sendUpdates": "none",
    },
)
```
