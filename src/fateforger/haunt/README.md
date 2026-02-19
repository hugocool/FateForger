# Haunt

Scheduling, reminders, and follow-up orchestration.

## Status

- Reminder orchestration: Implemented
- Planning anchor reconciliation: Implemented
- Planning session ID/store lookup hierarchy + strict fallback disambiguation: Implemented, Tested

Key files:
- `service.py`: runtime service wiring for reminders.
- `orchestrator.py`: haunt orchestration and dispatch.
- `planning_guardian.py`: daily planning guardrails.
- `reconcile.py`: calendar reconciliation for planning anchors.
- `planning_session_store.py`: local planning-session identity cache (user/date/event_id/status).
- `messages.py`: message models.
- `tools.py`: tool definitions for the haunt service.
- `stores`: see `planning_store.py`, `settings_store.py`, `event_draft_store.py`.

Notes:
- Haunt FunctionTool interfaces are strict-schema compatible; nullable inputs are explicit tool arguments.
