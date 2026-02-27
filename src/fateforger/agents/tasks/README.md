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
