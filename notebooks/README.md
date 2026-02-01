# Notebooks

## Status

- **Documented** (2026-01-26): Calendar MCP tool discovery + `fields` allowlist probing notes added.

## Purpose

This folder contains exploratory and development notebooks that should import application code from `src/`
without `sys.path` hacks. Ensure VS Code/Jupyter uses the Poetry virtualenv at `.venv/`.

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
