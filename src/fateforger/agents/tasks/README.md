# Tasks Agent

Task triage and execution agent (Task Marshal).

## Status

- Implemented: `TasksAgent` now uses a dedicated TickTick list-management tool (`manage_ticktick_lists`).
- Implemented: Tool-assisted multi-mention task resolution with exhaustive candidate scoring (`resolve_ticktick_task_mentions`).
- Implemented: sprint-focused Notion tools for search/filter, relation linking, and patch-style page edits (`find_sprint_items`, `link_sprint_subtasks`, `patch_sprint_page_content`).
- Implemented: opinionated sprint patch commands for single-event and bulk-event flows (`patch_sprint_event`, `patch_sprint_events`).
- Documented: list-management behavior and ownership are documented in this folder.
- Tested: unit and Slack handoff tests cover tool behavior and routing.
- User-confirmed working: pending.

Key files:
- `agent.py`: agent logic and prompts.
- `list_tools.py`: structured TickTick list/item operations and MCP call orchestration.
- `notion_sprint_tools.py`: Notion sprint-domain tools and patch preview/apply logic.
- `AGENTS.md`: operational rules for this subtree.

Notes:
- Uses TickTick MCP via `manage_ticktick_lists` when `TICKTICK_MCP_URL` is configured.
- Pending-task snapshots fail closed (empty result) when TickTick MCP is unreachable, so timeboxing can continue without noisy hard failures.
- Uses Notion MCP for sprint-domain operations via the task tools above.
- Notion sprint discovery supports single-source and multi-source defaults via:
  `NOTION_SPRINT_DATA_SOURCE_URL` / `NOTION_SPRINT_DB_ID` and
  `NOTION_SPRINT_DATA_SOURCE_URLS` / `NOTION_SPRINT_DB_IDS`.
- Tool contracts are strict-schema compatible; optional inputs must be passed explicitly as `null`.

## MECE Capability Map (Target)

### 1) TickTick Task Surface
- Discover projects/lists (`show_lists`) and open tasks per list or across all lists (`show_list_items` with null list selectors).
- Resolve ambiguous task mentions across projects (`resolve_ticktick_task_mentions`) for safe follow-up actions.
- Mutate TickTick state only through explicit operations (`create_list`, `add_items`, `update_items`, `remove_items`, `delete_items`, `delete_list`).

### 2) Notion Sprint Surface
- Discover sprint records (`find_sprint_items`) with explicit source selection or configured defaults.
- Manage parent/child sprint relations (`link_sprint_subtasks`).
- Patch sprint page content with safety rails (`patch_sprint_page_content`, plus single/bulk wrappers `patch_sprint_event` and `patch_sprint_events`).

### 3) Inter-Agent Planning Surface
- Provide bounded pending-task snapshots for timeboxing (`PendingTaskSnapshotRequest` -> `PendingTaskSnapshot`).
- Provide assist-turn delegation from timeboxing to tasks without changing timeboxing stage ownership.

### 4) Orchestration and Safety Surface
- Keep MCP IO bounded inside tool managers (`list_tools.py`, `notion_sprint_tools.py`).
- Keep tool schemas strict and explicit (`null` for unused optionals).
- Keep ambiguity non-destructive: ask targeted follow-ups instead of guessing identifiers.

## Destructive vs Non-Destructive Operations

- Non-destructive TickTick operations:
  - `show_lists`
  - `show_list_items`
  - `resolve_ticktick_task_mentions`
- Potentially destructive TickTick operations:
  - `create_list`
  - `add_items`
  - `update_items`
  - `remove_items`
  - `delete_items`
  - `delete_list`
- Non-destructive Notion operations:
  - `find_sprint_items`
  - `patch_sprint_page_content` with `dry_run=true`
  - `patch_sprint_event` with `dry_run=true`
  - `patch_sprint_events` with `dry_run=true`
- Potentially destructive Notion operations:
  - `link_sprint_subtasks` (relation writes)
  - `patch_sprint_page_content` with `dry_run=false`
  - `patch_sprint_event` / `patch_sprint_events` with `dry_run=false`
