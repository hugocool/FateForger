# Timeboxing Agent Notes

**Scope:** Operational rules for the `src/fateforger/agents/timeboxing/` subtree.
For file index, architecture, and status, see `README.md` in this folder.

> **Retired (2026-09-09).** Most of what this file used to say rules for —
> the coordinator (`agent.py`), GraphFlow orchestration (`flow_graph.py`,
> `nodes/`), the LLM-facing plan/patch models (`tb_models.py`, `tb_ops.py`),
> the sync engine (`sync_engine.py`, `submitter.py`), the schema-in-prompt
> patcher (`patching.py`), and the Notion-backed NLU/constraint plumbing
> (`nlu.py`, `constraint_retriever.py`, `constraint_search_tool.py`,
> `notion_constraint_extractor.py`) — is deleted code
> (`refactor: retire TimeboxingFlowAgent and the 34 modules only it
> reached`, commit `67489cd`; see the top of `README.md` for the full list).
> Below are the rules that still apply to what remains: the Stage 1
> elicitation loop and the durable constraint-memory backends. Everything
> those old sections said about the deleted subsystems is not repeated here
> as a historical record — read `67489cd^` for that, the way the two root
> calendar docs point at their own predecessor code instead of re-describing
> it.

## Stage 1 Elicitation (elicitation.py, elicitation_judges.py)

- Stage 1's gate (`stage1_gate` in `elicitation.py`) is arithmetic over the session snapshot and never calls a model; only the three judges in `elicitation_judges.py` (via `elicit()`) touch a model client, and only from the Slack host's `resolve()` in `slack_bot/timeboxing_host.py`.
- A cell whose probe was answered (an `ELICITED_STATEMENT` fact carrying that cell id) or assumed past (a `PlannerAssumption`) is never asked again. `closed_cells` is the single source of that subtraction; read it there rather than re-deriving "closed" at a call site, or the gate could disagree with itself about whether a cell is still open depending on who asked.
- A Stage 1 judge failure (a bad schema, an index the model was not offered, an empty option label) propagates out of `elicit()` rather than degrading to a smaller matrix or a silently skipped cell. A host that cannot judge fails the turn instead of proposing to close a stage it never opened.
- `elicit()`'s classify batch runs every open cell concurrently and completes in full -- via `asyncio.gather`, so any one failure fails the whole batch -- before the coverage matrix is assembled and written to the snapshot. Nothing reads a matrix that is still being built.

## Durable Constraint Memory (mcp_clients.py, graphiti_constraint_memory.py, constraint_record_memory.py, kg_constraint_client.py, durable_constraint_store.py)

- These backends are read by `fateforger.agents.tasks.defaults_memory` (tasks' defaults memory) and by `fateforger.core.runtime`'s startup checks via `settings.timeboxing_memory_backend` — not by any coordinator in this directory, which no longer exists.
- `mcp_clients.py` now holds only `ConstraintMemoryClient` (the constraint-memory MCP stdio client); `McpCalendarClient` was deleted with the coordinator.
- `kg_constraint_client.py` is read-only, deliberately: a constraint in the standalone memory server's store (`data/memory.db`) is L2 -- never authored directly, always projected from the immutable observation log -- so writing a row straight into that store would bypass the projection that makes re-projection-on-judgement-improvement possible.
- `durable_constraint_store.py` defines the backend-neutral `DurableConstraintStore` protocol the concrete clients above implement; keep new durable-memory backends behind that same interface rather than special-casing a backend name at a call site.

## Forbidden: Deterministic NLU

- Do not add deterministic extraction/interpretation of user intent from free-form text (scope/date/intent classification).
- Never post-process LLM prose with phrase/substring/regex filters to drive behavior or suppress content. If behavior needs control, put it in typed schema fields and state transitions.
- This rule outlives any one module: it applied to the deleted `nlu.py` and applies equally to the elicitation judges and any future module in this directory.

## Task Sources

- TickTick MCP task-fetching (`TICKTICK_MCP_URL`) has moved to `fateforger/agents/tasks/` (see `agents/tasks/README.md`); it is not this directory's concern any more.

## Implementation Ticket

- `TICKET_SYNC_ENGINE.md` (repo root) is the retired sync engine's implementation ticket -- historical, not a live checklist for this directory.
