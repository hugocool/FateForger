# Tasks Agent — Agent Notes

**Scope:** `src/fateforger/agents/tasks/`

## Tooling

- Use the dedicated `manage_ticktick_lists` `FunctionTool` for TickTick list/item operations.
- Keep TickTick MCP IO in `list_tools.py`; do not spread MCP call choreography into Slack handlers.
- Keep Notion sprint MCP IO in `notion_sprint_tools.py`; expose sprint workflows through `find_sprint_items`, `link_sprint_subtasks`, and `patch_sprint_page_content`.
- Default to `model="project"` unless the user provides explicit parent-task context for `model="subtask"`.

## Behavior

- If list or item resolution is ambiguous, return structured ambiguity and ask a focused follow-up.
- Do not perform destructive list/item operations when ambiguity exists.
- For Notion sprint page edits, default to dry-run previews and fail safely on ambiguous/conflicting matches.
- Keep assistant responses short and actionable; include operation outcome counts.
- Guided refinement session v0 is a gated 4-phase flow (`scope` -> `scan` -> `refine` -> `close`).
- Start commands are explicit (`/task-refine`, `start guided task refinement session`, `start task refinement session`, `start scrum refinement session`).
- Cancel commands are explicit (`cancel|stop|exit task refinement session`).
- During guided session turns, only advance phase when `gate_met=true`; otherwise remain in current phase and request missing fields.
- On close gate success, persist a per-user recap in-memory and expose it via `GuidedRefinementRecapRequest`.
- Keep guided flow focused on refinement quality (state, AC, DoD, sizing, dependencies, next action); do not turn it into scheduling.

## Testing

- Add/update unit tests under `tests/unit/` for operation behavior and error paths.
- Keep Slack routing behavior covered by existing handoff tests (unit/e2e).
- Guided refinement changes must include tests for:
  - start/cancel commands
  - gate not met (phase held)
  - gate met (phase advance)
  - close recap persistence/retrieval
