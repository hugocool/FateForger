# Timeboxing Agent

Stage-gated timeboxing workflow that builds daily schedules via conversational refinement and syncs to Google Calendar.

> **Retired (2026-09-09).** The coordinator this file describes
> (`agent.py`'s `PlanningCoordinator`), its `GraphFlow` orchestration
> (`flow_graph.py`, `nodes/`), the sync engine (`sync_engine.py`,
> `calendar_reconciliation.py`, `submitter.py`), the schema-in-prompt patcher
> (`patching.py`), the LLM-facing plan/patch models (`tb_models.py`,
> `tb_ops.py`, `timebox.py`), the Notion-backed constraint plumbing
> (`constraint_retriever.py`, `constraint_search_tool.py`,
> `notion_constraint_extractor.py`), and the calendar MCP client
> (`McpCalendarClient`, formerly in `mcp_clients.py`) are all deleted —
> `refactor: retire TimeboxingFlowAgent and the 34 modules only it reached`
> (commit `67489cd`). Slack may still carry buttons from cards that flow
> posted; `slack_bot/retired_cards.py` rewrites a press to say the flow is
> retired instead of dispatching here. What remains in this directory is a
> different, newer path: the Stage 1 elicitation loop (`elicitation.py`,
> `elicitation_judges.py`, driven from `slack_bot/timeboxing_host.py`) and
> the durable constraint-memory backends (`graphiti_constraint_memory.py`,
> `constraint_record_memory.py`, `kg_constraint_client.py`,
> `durable_constraint_store.py`, and `mcp_clients.py`'s surviving
> `ConstraintMemoryClient`), which are now read by
> `fateforger.agents.tasks.defaults_memory` (tasks' defaults memory) and by
> `runtime.py`'s startup checks — not by any timeboxing coordinator, which no
> longer exists. The Status table, File Index, and Architecture sections
> below have been corrected in place, not kept as a historical record like
> the two root calendar docs, because most of this file described modules
> that no longer exist.

## Status

| Subsystem | Status | Tests | Confirmed |
|-----------|--------|-------|-----------|
| Domain models (tb_models, tb_ops) | Retired 2026-09-09 — `tb_models.py`/`tb_ops.py` deleted with the coordinator | — | `67489cd` |
| Sync engine (sync_engine, submitter, calendar_reconciliation) | Retired 2026-09-09 — deleted with the coordinator | — | `67489cd` |
| Patching (schema-in-prompt) | Retired 2026-09-09 — `patching.py` deleted with the coordinator | — | `67489cd` |
| GraphFlow orchestration | Retired 2026-09-09 — `flow_graph.py` and the `nodes/` package deleted with the coordinator | — | `67489cd` |
| Skeleton pre-generation (AC1) | Retired 2026-09-09 — deleted with the coordinator; its test (`test_timeboxing_skeleton_pre_generation.py`) went with it | — | `67489cd` |
| Calendar sync + undo controls | Retired at the Slack layer (2026-09-09): pressing a Stage 5 card button rewrites it with a "this flow is retired" message instead of dispatching to the agent -- see `retired_cards.py`. The agent code itself (`agent.py`) is deleted too, in the same day's follow-up retirement commit. | `test_retired_cards.py` | `67489cd` |
| Durable profile/date-span constraint auto-upsert + Stage 1 prefetch wait | Retired 2026-09-09 — the write path (`agent.py`'s `_upsert_constraints_to_durable_store`) is deleted; its test (`test_timeboxing_durable_constraints.py`) went with it. `mcp_clients.py`'s payload decoding still has its own test (row below). | — | `67489cd` |
| Graphiti durable memory cutover (Neo4j-backed MCP, no Mem0/file fallback) | Implemented, Tested — read via `settings.timeboxing_memory_backend`, now by `runtime.py`'s startup checks and `fateforger.agents.tasks.defaults_memory` (tasks' defaults memory), not by the deleted coordinator | `test_graphiti_constraint_memory.py`, `test_settings_mcp_endpoints.py`, `test_runtime_mcp_startup_checks.py` | 2026-03-10 |
| Constraint-memory MCP payload decoding hardening | Implemented, Tested — `mcp_clients.py`'s `ConstraintMemoryClient`, read by tasks' defaults memory | `test_timeboxing_constraint_memory_client_tool_name.py` | — |
| Stage 1 elicitation loop (concern-floor coverage matrix, three judges, arithmetic gate) | Implemented, Tested (see [Stage 1 Elicitation](#stage-1-elicitation)) | `test_elicitation_gate.py`, `test_elicitation_judges.py`, `test_elicitation_composes.py`, `tests/evals/test_stage1_elicitation.py` | 2026-09-06 |
| Stage 3 markdown-first skeleton overview | Retired 2026-09-09 — deleted with the coordinator; its test (`test_timeboxing_skeleton_draft_contract.py`) went with it | — | `67489cd` |
| Stage 4 advisory quality facts (0-4) | Retired 2026-09-09 — deleted with the coordinator; its test (`test_phase4_rewiring.py`) went with it | — | `67489cd` |
| Deterministic stage action buttons | Retired at the Slack layer (2026-09-09), same as the row above -- see `retired_cards.py` | `test_retired_cards.py` | — |
| Structured-output strict tool contract | Retired 2026-09-09 — `constraint_search_tool.py` and the `TimeboxingFlow` it validated are deleted; both tests (`test_timeboxing_constraint_search_tool_strict.py`, `test_timeboxing_flow.py`) went with it | — | `67489cd` |

## File Index

Everything the coordinator owned (orchestration, domain models, calendar
sync, patching, prompt engineering, the Notion-backed NLU/constraint
plumbing, Slack routing utilities, and the `nodes/` subfolder) was deleted
2026-09-09 with `TimeboxingFlowAgent` — see the retirement note above for the
file list and the commit. What is left in this directory is the durable
constraint-memory backends and the Stage 1 elicitation loop, both of which
now live and are called from elsewhere (tasks' defaults memory,
`runtime.py`, and `slack_bot/timeboxing_host.py`), plus a newer,
undocumented-here artifact-led planning-session module
(`adaptive_timeboxing.py`, `session_contracts.py`, `readiness.py`,
`required_blocks.py`, `day_frame.py`, `feedback.py`).

### Durable Constraint Memory

| File | Responsibility |
|------|---------------|
| `mcp_clients.py` | `ConstraintMemoryClient` (the constraint-memory MCP stdio workbench client). `McpCalendarClient` used to live here too; it was deleted with the coordinator. Read by `fateforger.agents.tasks.defaults_memory`, not by any timeboxing coordinator. |
| `graphiti_constraint_memory.py` | Graphiti durable-memory adapter (Graphiti MCP transport; Neo4j-backed deployment config). Read via `settings.timeboxing_memory_backend` by `runtime.py`'s startup checks and by `fateforger.agents.tasks.defaults_memory`. |
| `constraint_record_memory.py` | Backend-neutral durable constraint serialization/query/update contract used by the Graphiti adapter. |
| `kg_constraint_client.py` | Read-only client onto the standalone memory server's own store (`data/memory.db`), speaking the same `DurableConstraintStore` contract as the Graphiti adapter — the replacement for the Notion-backed `constraint_mcp` reads that used to 404. |
| `durable_constraint_store.py` | The `DurableConstraintStore` protocol: one small backend-neutral interface the concrete durable-memory clients above implement, so `runtime.py` and tasks' defaults memory stay backend-neutral. |
| `preferences.py` | `Constraint`/`ConstraintBase` models and their enums (necessity, status, source, scope). The session-store persistence class that once lived here (`ConstraintStore`) had no writer left after the legacy agent retired (2026-09-09) and was removed. |

### Stage 1 Elicitation

| File | Responsibility |
|------|---------------|
| `elicitation.py` | The Stage 1 concern floor and the arithmetic gate. `CONCERNS` is the one authored list (seven rows, including `method` for rules about the planning itself); with the two non-concern rows `unplaced` and `request` that's nine rows total, each crossed with the five `CRITERIA` into a `CoverageMatrix`. `ranked_open_cells` orders what is still open, `stage1_gate` is the gate the kernel and the interpreter both read, and `closed_cells` tracks cells this session will not ask again. Calls no model. |
| `elicitation_judges.py` | The three judgements that fill the matrix each turn -- `PlacementJudge` (files anchors and unanchored rules under a row), `CoverageJudge` (one verdict per open cell), `ProbeJudge` (phrases the question and up to four option buttons for the top open cells) -- and `elicit()`, the orchestrator `HostPlanningContext._frame_from_corpus` in `slack_bot/timeboxing_host.py` calls once per Stage 1 turn on its own model client (`agent_type="timeboxing_judge"`; see `docs/reference/setup/llm.md`). |

Design: `docs/superpowers/specs/2026-09-05-stage1-elicitation-loop-design.md`.
Measurements: `docs/superpowers/research/2026-09-06-stage1-loop-evals.md`.

## Architecture

The Coordinator + Stage Agents / GraphFlow / Stage Pipeline / Session State /
Model Hierarchy / Event Identity / Patching (Schema-in-Prompt) / TOON Prompt
Injection sections that used to sit here described `agent.py`'s
`PlanningCoordinator`, `flow_graph.py`'s GraphFlow DAG, and the `nodes/`
stage agents — all deleted 2026-09-09 (`67489cd`, see the retirement note at
the top of this file). Slack's `/timebox` slash command and the button flow
it drove now dead-end at `retired_cards.py` rather than reaching an agent in
this directory; `src/fateforger/slack_bot/planning.py` has its own,
unrelated `PlanningCoordinator` for the Planning/Scheduling UI card flow
(see `slack_bot/README.md`'s "Proposal Object Interaction Contract"), and is
not a continuation of this module's coordinator. What that architecture
looked like is in git history at `67489cd^`, not repeated here as a
historical record, because the two root calendar docs already show that
pattern and a second copy would invite editing a description of dead code
instead of reading the commit.

What survives here is the Stage 1 elicitation loop (`elicitation.py`,
`elicitation_judges.py` — see [Stage 1 Elicitation](#stage-1-elicitation)
above and the design/measurement docs it links), the durable
constraint-memory backends (see the File Index above), and a newer
artifact-led planning-session module (`adaptive_timeboxing.py`,
`session_contracts.py`, `readiness.py`, `required_blocks.py`,
`day_frame.py`, `feedback.py`) that this README does not yet document in
architectural terms.

## Related Files (Outside This Folder)

| File | Role |
|------|------|
| `src/fateforger/slack_bot/handlers.py` | Central Slack event/action router. No longer routes to a coordinator in this directory (see the Architecture note above); still owns `/timebox` and dispatches retired-card presses to `retired_cards.py`. |
| `src/fateforger/slack_bot/timeboxing_commit.py` | Stage 0 Slack UI (day picker + confirm button). Its target coordinator is deleted; see `slack_bot/README.md`'s Timeboxing UI file index. |
| `TICKET_SYNC_ENGINE.md` | Implementation ticket (repo root) for the now-deleted sync engine — historical. |
| `notebooks/phase5_integration_test.ipynb` | Live MCP + LLM integration tests written against the deleted coordinator — not verified against current code. |

`src/fateforger/llm/toon.py`, the TOON tabular encoder this section used to
cite, is deleted too; no surviving module in this directory imports it.

## How to Run Tests

```bash
# Durable constraint-memory backends
poetry run pytest tests/unit/constraints/test_graphiti_constraint_memory.py \
  tests/unit/constraints/test_timeboxing_constraint_memory_client_tool_name.py \
  tests/unit/core/test_settings_mcp_endpoints.py \
  tests/unit/core/test_runtime_mcp_startup_checks.py -v

# Stage 1 elicitation loop
poetry run pytest tests/unit/timeboxing/test_elicitation_gate.py \
  tests/unit/timeboxing/test_elicitation_judges.py \
  tests/unit/timeboxing/test_elicitation_composes.py -v

# All timeboxing-related tests
poetry run pytest tests/unit/ -k timeboxing -v
```
