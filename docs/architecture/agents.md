---
title: Agents
---

> **Retired (2026-09-09).** `TimeboxingFlowAgent` (`agents/timeboxing/agent.py`),
> `ConstraintRetriever` (`agents/timeboxing/constraint_retriever.py`), and the
> Notion-backed `ConstraintExtractorAgent` are deleted with the coordinator
> that owned them — `refactor: retire TimeboxingFlowAgent and the 34 modules
> only it reached` (commit `67489cd`). The sections below described those
> three; see `docs/indices/agents_timeboxing.md` for the file-by-file
> retirement note. What follows is the three components that actually plan
> and write a day now.

## Adaptive timeboxing kernel

Artifact-led planning-session orchestration: the Stage 1 elicitation loop
(coverage matrix, arithmetic gate, three judges) plus the newer
artifact-led session state (`session_contracts.py`, `readiness.py`,
`required_blocks.py`, `day_frame.py`, `feedback.py`). The kernel decides
what a planning turn does but takes timezone, calendar, and constraint-store
access as ports; it is driven from the Slack host, which supplies those
ports and does the actual Slack routing.

Code:
- `src/fateforger/agents/timeboxing/adaptive_timeboxing.py`
- `src/fateforger/slack_bot/timeboxing_host.py` (the host that supplies the kernel's ports and calls it per Stage 1 turn)

Related docs:
- `docs/indices/agents_timeboxing.md`
- `docs/superpowers/specs/2026-09-05-stage1-elicitation-loop-design.md`

## Harness planner (DeepSeek)

Host-owned context boundary for adaptive planning turns: refreshes the
constraint and calendar read models for the locked day and hands one
complete brief to a fresh harness run. Reads durable constraints via
`kg_constraint_client.py`, the read-only client onto the standalone memory
server's own store (`data/memory.db`), speaking the `DurableConstraintStore`
protocol `durable_constraint_store.py` defines.

Code:
- `src/fateforger/slack_bot/deepseek_timebox_planner.py`
- `src/fateforger/agents/timeboxing/kg_constraint_client.py`
- `src/fateforger/agents/timeboxing/durable_constraint_store.py`

## tmbx server (calendar writes)

MCP server exposing the level 1 timebox tools (`plan_read`, `plan_apply`,
`plan_commit`, `plan_undo`, `plan_history`) that read and write the day's
Google Calendar events. A write path can refuse (reported as a normal JSON
result with a `"reason"` code, never raised as an exception) rather than
silently applying a stale or conflicting patch.

Code: `src/tmbx/server.py`

Related docs: `src/tmbx/` module docstrings; `tickets/` entries under `tmbx`.
