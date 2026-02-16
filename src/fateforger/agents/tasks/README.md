# Tasks Agent

Task triage and execution agent (Task Marshal).

## Status

- Implemented: `TasksAgent` now uses a dedicated TickTick list-management tool (`manage_ticktick_lists`).
- Documented: list-management behavior and ownership are documented in this folder.
- Tested: unit and Slack handoff tests cover tool behavior and routing.
- User-confirmed working: pending.

Key files:
- `agent.py`: agent logic and prompts.
- `list_tools.py`: structured TickTick list/item operations and MCP call orchestration.
- `AGENTS.md`: operational rules for this subtree.

Notes:
- Uses TickTick MCP via `manage_ticktick_lists` when `TICKTICK_MCP_URL` is configured.
