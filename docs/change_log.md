---
title: Change Log
---

## Docs Refactor

- Removed legacy summary files.
- Created setup, architecture and reference sections.

## Timeboxing Refactor (2026-01)

- Split timeboxing prompts by stage and introduced a single-purpose skeleton drafting template.
- Added typed stage contexts and explicit coordinator injection for constraints + immovables.
- Hardened `Timebox.schedule_and_validate` for minimal JSON outputs.
- Switched list-shaped prompt injection (constraints/tasks/immovables/events) to TOON tables for token-efficiency and clearer schemas.

Pointers:

- `TIMEBOXING_REFACTOR_REPORT.md`
- `docs/architecture/timeboxing_refactor.md`
