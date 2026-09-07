# Task board over MCP (#241) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

## Why

`docs/superpowers/specs/2026-08-31-linking-materials-to-blocks.md` needs a child subagent that can answer "which ticket did Hugo mean" with real task tools behind a `toolFilter`. `toolFilter` matches MCP tool names, so the task tools must exist over MCP. Two facts measured on 2026-09-05 reshape the ticket:

1. The bot's Notion MCP (docker container `admonish-1-notion-mcp-1`, `http://localhost:3001/mcp`, bearer token `MCP_HTTP_AUTH_TOKEN`) is the official Notion MCP server in REST mode. Its tools are `API-post-database-query`, `API-retrieve-a-page`, `API-post-search`, and so on. It does **not** expose `notion-search`/`notion-fetch`, so `NotionSprintManager.find_sprint_items` (aliases `notion-search`, `notion_search`, `search`) cannot reach it at all. Wrapping it would expose a dead path. This plan builds a new read-only facade over the REST tools instead.
2. The harness MCP client plugin has no tool allow-list and `tools.restrict()` refuses a global scope, so anything mounted in the profile reaches the planner's prompt. The mount therefore ships **disabled by default** and the schema is kept to two tiny tools.

Board facts, verified live 2026-09-05:

| thing | id |
|---|---|
| Tasks database (REST `database_id`) | `110baca1-857f-48b1-a8ec-cea325202eef` (data source `collection://4ab03ce6-e7bc-41bd-8e08-0bafa091083e`) |
| Sprints database (REST `database_id`) | `0e34a9da-1fe6-4cd2-a2fc-c36c0ae688b0` (data source `collection://8d80242d-fdf9-4bdb-90b4-761ad1cea6d5`) — the one the Tasks `Sprint` relation targets. `collection://5a086bf6-…` is a stale wrapper; do not use it. |
| Current sprint page (today) | `30828174-6a47-80c3-8665-e0755595a48c` "Sprint 8 - product" |
| Ready tickets | 79 total, 12 in the current sprint |

Task property shapes returned by `API-post-database-query` (one row is ~7.6 KB raw):

- `Task ID`: `unique_id` → `{"prefix": null, "number": 500}`
- `Task name`: `title` → rich text array
- `Status`: `status` → `{"name": "Not started"}`; options Not started / In progress / Done / Archived
- `Ticket Status`: `select` → `{"name": "Ready"}`; options Unrefined / Refined / Ready / Paused / Zombie / Blocked
- `Priority`: `select` Low / Medium / High; `Estimates`: `select` XS / S / M / L / XL
- `Due`: `date` → `{"start": "...", "end": ...}` or null
- `DoD`, `Summary`: `rich_text` arrays
- `Blocked by`, `Parent-task`, `Sub-tasks`, `Project`, `Sprint`: `relation` → `[{"id": ...}]`
- `Work Type`: `rollup` → `{"type": "array", "array": [{"type": "select", "select": {"name": "Product"}}]}` (empty when no Project)
- `Output Artifact`: `url`
- row also carries `id`, `url`, `last_edited_time`

Sprint filter that works: `{"property": "Sprint status", "status": {"equals": "Current"}}` on the Sprints database. Task filters that work: `{"property": "Ticket Status", "select": {"equals": "Ready"}}`, `{"property": "Sprint", "relation": {"contains": "<sprint page id>"}}`, combined under `{"and": [...]}`. Sort `[{"property": "Priority", "direction": "descending"}]` puts High first. Pagination: `page_size` (max 100), `start_cursor`; response carries `has_more`, `next_cursor`.

## Global constraints

- **No `re`, no keyword lists, no substring or fuzzy matching over user content** (CLAUDE.md). Selection is by Notion's own structured filters; any "which ticket" judgement belongs to the model that calls these tools, never to this code. Truncating a string by length is fine; comparing it is not.
- **Read-only.** Nothing here calls a Notion tool that writes. The server exposes exactly two tools.
- **No autogen import in the stdio server or the facade.** Use the `mcp` client library directly (`mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession`), as the memory server does. The facade takes an injectable async `call_tool(name, arguments) -> str` so tests need no network.
- **Every failure is loud.** A missing token, an unreachable server, zero or several "Current" sprints, or a malformed row raise with a message that names the cause. Never return an empty list for an error.
- **Tool names** must match `^[A-Za-z0-9_-]+$` (DeepSeek function-name contract) and be allow-listable as `mcp__task_board__<name>`.
- Tests run from the worktree with the parent venv: `cd <worktree> && PYTHONPATH=src ../../.venv/bin/python -m pytest <files> -q`. There is no `.env` in the worktree; unit tests must not need one.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Task 1 — the read-only board facade

**Files:** `src/fateforger/agents/tasks/board.py` (new), `src/fateforger/core/config.py` (two fields), `tests/unit/test_task_board.py` (new).

Settings, added to `Settings` in `core/config.py` beside the existing Notion fields:

```python
notion_tasks_database_id: str = Field(default="110baca1-857f-48b1-a8ec-cea325202eef")
notion_sprints_database_id: str = Field(default="0e34a9da-1fe6-4cd2-a2fc-c36c0ae688b0")
```

Do not reuse `notion_sprint_db_id`; its name is ambiguous and its default is empty.

Models (pydantic, in `board.py`):

```python
class SprintRef(BaseModel):
    page_id: str
    name: str
    status: str            # "Current"
    start: str | None = None   # ISO date from Dates.start
    end: str | None = None

class TaskRow(BaseModel):
    page_id: str
    number: int | None          # Task ID
    name: str
    status: str                 # Status.name
    ticket_status: str | None   # Ticket Status.name
    priority: str | None
    estimate: str | None
    due: str | None             # Due.start
    blocked_by: list[str]       # page ids
    parent_id: str | None
    project_id: str | None      # first Project relation id
    work_type: str | None       # first Work Type rollup select name
    sprint_id: str | None
    summary: str                # Summary plain text, truncated to 200 chars
    dod: str                    # DoD plain text, truncated to 300 chars
    url: str
    last_edited: str

class TaskPage(BaseModel):
    """One ticket in full: the row plus untruncated summary and dod."""
    row: TaskRow
    summary: str
    dod: str
```

Facade:

```python
Scope = Literal["current_sprint_ready", "current_sprint", "ready", "open"]

class TaskBoard:
    def __init__(self, *, call_tool: CallTool, tasks_database_id: str, sprints_database_id: str) -> None: ...
    async def current_sprint(self) -> SprintRef: ...
    async def list_tasks(self, scope: Scope, *, limit: int = 25, cursor: str | None = None) -> TaskListing: ...
    async def get_task(self, page_id: str) -> TaskPage: ...

class TaskListing(BaseModel):
    scope: str
    sprint: SprintRef | None     # set for the two current_sprint scopes
    tasks: list[TaskRow]
    next_cursor: str | None
```

`CallTool = Callable[[str, dict[str, Any]], Awaitable[str]]` returning the tool's text content (JSON). A module-level `async def notion_call_tool(name, arguments) -> str` opens a session against `get_notion_mcp_url()` with `get_notion_mcp_headers()` (from `fateforger.tools.notion_mcp`) and calls the tool once; `TaskBoard.from_settings()` classmethod wires it with `settings.notion_tasks_database_id` / `settings.notion_sprints_database_id`. When `MCP_HTTP_AUTH_TOKEN` is absent from the environment, fall back to `settings.mcp_http_auth_token` only if it is not the placeholder default; otherwise raise `TaskBoardUnavailable("no Notion MCP auth token")`.

Filters per scope (exact REST filter JSON):

- `ready`: `{"property": "Ticket Status", "select": {"equals": "Ready"}}`
- `current_sprint`: `{"property": "Sprint", "relation": {"contains": <current sprint page id>}}`
- `current_sprint_ready`: `{"and": [ready filter, current_sprint filter]}`
- `open`: `{"and": [{"property": "Status", "status": {"does_not_equal": "Done"}}, {"property": "Status", "status": {"does_not_equal": "Archived"}}]}`

Every listing sends `sorts: [{"property": "Priority", "direction": "descending"}]`, `page_size: min(limit, 100)`, and `start_cursor` when a cursor is given. `current_sprint()` queries the Sprints database with the Current filter and `page_size: 2`; zero rows raises `NoCurrentSprint`, two rows raises `AmbiguousCurrentSprint` naming both page ids. `get_task` calls `API-retrieve-a-page` with `{"page_id": page_id}`.

Row mapping is a pure function `row_from_page(page: dict) -> TaskRow` plus `page_from_page(page: dict) -> TaskPage`. Plain text of a rich text array is the concatenation of each item's `plain_text`. A property absent from the page maps to `None`/empty, never raises; a page missing `id` or `properties` raises `MalformedPage`.

Errors: `TaskBoardError(RuntimeError)` base; `TaskBoardUnavailable`, `NoCurrentSprint`, `AmbiguousCurrentSprint`, `MalformedPage` subclasses. A tool result that is not valid JSON, or that carries an `object: "error"` envelope, raises `TaskBoardError` with the server's message.

Tests (`tests/unit/test_task_board.py`), with a fake `call_tool` that records `(name, arguments)` and returns canned JSON:

1. `test_current_sprint_filters_on_status_current` — the Sprints database id and the exact Current filter are sent; a `SprintRef` with name and dates comes back.
2. `test_no_current_sprint_raises` / `test_two_current_sprints_raise_naming_both`.
3. `test_ready_scope_sends_the_ticket_status_filter`, `test_current_sprint_ready_combines_both_filters_under_and`, `test_open_scope_excludes_done_and_archived` — assert the exact filter JSON, the sort, and `page_size`.
4. `test_cursor_is_forwarded_and_next_cursor_returned`.
5. `test_row_from_page_maps_every_field` using a fixture built from the real shapes above (include a Work Type rollup with one select, a Due with start, two Blocked by ids). `test_row_from_page_tolerates_missing_properties`. `test_summary_and_dod_are_truncated_in_rows_but_full_in_page`.
6. `test_error_envelope_raises_with_the_server_message`; `test_missing_token_raises_unavailable` (monkeypatch env and settings).
7. An AST test `test_board_uses_no_pattern_matching`: parse `board.py`, assert no `import re` and no call to `str.lower`, `str.split`, `difflib` or `in` comparisons... keep it to imports: assert neither `re` nor `difflib` is imported.

Commit: `feat(tasks): a read-only board facade over the live Notion REST tools (#241)`.

## Task 2 — the two-tool stdio MCP server

**Files:** `src/fateforger/slack_bot/task_board_mcp.py` (new), `tests/unit/test_task_board_mcp.py` (new).

Model on `slack_bot/planning_result_mcp.py` and `slack_bot/timebox_progress_mcp.py`: a `FastMCP(name="task-board", instructions=...)`, `mcp.run()` under `__main__`. Two tools, no more:

```python
@mcp.tool(name="task_board_list")
async def task_board_list(scope: Literal["current_sprint_ready", "current_sprint", "ready", "open"], limit: int = 25, cursor: str | None = None) -> dict: ...

@mcp.tool(name="task_board_get")
async def task_board_get(page_id: str) -> dict: ...
```

`task_board_list` returns `TaskListing.model_dump(mode="json")`; `task_board_get` returns `TaskPage.model_dump(mode="json")`. The board is built lazily once per process via `TaskBoard.from_settings()` behind a module function `_board()` that tests can monkeypatch. Errors propagate as `ToolError` with the facade's message (raise, never return a polite dict). The `instructions` string tells the caller that the ids returned are Notion page ids, that they are the only identity to pass onward, and that it must not guess a ticket from its title if the listing does not contain it.

Docstrings on both tools are short (the schema is paid for on every call it is mounted): one sentence on what comes back, one on the scope vocabulary.

Tests:

1. `test_exactly_two_tools_are_registered` — list the FastMCP tools; names are `task_board_list` and `task_board_get` and nothing else.
2. `test_tool_names_are_allow_listable` — every name matches the character set the harness accepts (check each character is alnum, `_` or `-`; this is an identifier this system minted).
3. `test_list_delegates_scope_limit_cursor` and `test_get_delegates_page_id` with a monkeypatched `_board()` returning a stub with recorded calls.
4. `test_facade_errors_surface_as_tool_errors` — a stub raising `NoCurrentSprint` becomes a `ToolError` whose message contains the cause.
5. `test_server_module_imports_no_autogen` — AST over the module source: no import whose root is `autogen_ext` or `autogen_agentchat`.

Commit: `feat(slack): task_board stdio MCP server — two read-only tools for a child subagent (#241)`.

## Task 3 — the profile mount, disabled by default, and the smoke script

**Files:** `infra/dsh/profile/cordis.patch.yml` (one new row after `mcp-planning-result`), `scripts/task_board_smoke.py` (new), `tests/unit/test_task_board_profile_mount.py` (new).

Profile row, same shape as `mcp-planning-result` (stdio, venv python, `-m fateforger.slack_bot.task_board_mcp`, `cwd` and `PYTHONPATH` through `FF_FATEFORGER_ROOT`), `serverName: task_board`, `toolCallTimeoutMs: 60000`, `failOnStartupError: true`, and:

```yaml
      disabled: !!js !process.env.FF_TASK_TOOLS
```

with a comment block stating, in the file's own register: the MCP client has no allow-list and `tools.restrict()` refuses a global scope, so a mounted server reaches the planner's prompt; the row is therefore off until the host sets `FF_TASK_TOOLS`, which only a turn that spawns the `find_material` child should do; the two tools cost about N tokens (measure with the profile's existing method and write the number); and the server is read-only. Do not touch any other row.

Smoke script `scripts/task_board_smoke.py`: loads `.env` from the repo root, builds `TaskBoard.from_settings()`, prints the current sprint, then the count and first three rows (number, name, ticket status, priority) for each scope. Exits non-zero with the exception message on failure. Manual, never run by tests.

Test `tests/unit/test_task_board_profile_mount.py`, following `tests/unit/test_dsh_profile_env_defaults.py`'s way of reading the profile: the row with `serverName: task_board` exists, its `args` name `fateforger.slack_bot.task_board_mcp`, and its `disabled` expression references `FF_TASK_TOOLS`. This inspects a file this repo minted, not user content.

Commit: `feat(dsh): mount the task_board server, disabled until a turn asks for it; smoke script (#241)`.

## Out of scope (record in the ticket, do not build)

- Repairing `NotionSprintManager`'s alias names against the live server.
- The marshal pre-pass job and the `find_material` child subagent.
- Any write to Notion.
