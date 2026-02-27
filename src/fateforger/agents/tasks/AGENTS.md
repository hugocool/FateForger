# Tasks Agent — Agent Notes

**Scope:** `src/fateforger/agents/tasks/`

## Tooling

- Use the dedicated `manage_ticktick_lists` `FunctionTool` for TickTick list/item operations.
- Keep TickTick MCP IO in `list_tools.py`; do not spread MCP call choreography into Slack handlers.
- Keep Notion sprint MCP IO in `notion_sprint_tools.py`; expose sprint workflows through `find_sprint_items`, `link_sprint_subtasks`, and `patch_sprint_page_content`.
- Default to `model="project"` unless the user provides explicit parent-task context for `model="subtask"`.

## MECE Capability Taxonomy

- **Task operations:** TickTick list/item CRUD and selection disambiguation.
- **Sprint operations:** Notion sprint search, relation linking, and page patch previews/applies.
- **Inter-agent contracts:** typed request/response messages for other agents (for example pending-task snapshots).
- **Assistant orchestration:** concise user-facing replies, tool routing, and bounded iteration.

## Behavior

- If list or item resolution is ambiguous, return structured ambiguity and ask a focused follow-up.
- Do not perform destructive list/item operations when ambiguity exists.
- For Notion sprint page edits, default to dry-run previews and fail safely on ambiguous/conflicting matches.
- Keep assistant responses short and actionable; include operation outcome counts.
- Keep inter-agent APIs typed (Pydantic models), not free-text protocols.
- Prefer `match` for operation routing where it keeps branches MECE.
- Keep broad `try/except` out of orchestration paths; contain exceptions at MCP IO boundaries.

## Refactor Audit (Mandatory)

- Before touching files in this folder:
  - run a pre-audit (`lines`, `if`, `try`, `match`) for touched modules,
  - map the change to the MECE taxonomy above.
- After tests pass:
  - run a post-audit with the same metrics,
  - record framework leverage and deletions/simplifications in the issue/PR checkpoint.

## Testing

- Add/update unit tests under `tests/unit/` for operation behavior and error paths.
- Keep Slack routing behavior covered by existing handoff tests (unit/e2e).
