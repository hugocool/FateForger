# Timeboxing Agent Notes

**Scope:** Operational rules for the `src/fateforger/agents/timeboxing/` subtree.
For file index, architecture, and status, see `README.md` in this folder.

## Goals

- Keep the timeboxing flow responsive; never block user replies on durable preference writes.
- Extract session-scoped constraints from user replies (not from generic "start timeboxing" requests).
- Prefetch durable constraints from Notion (via constraint-memory MCP) before Stage 1 so the cache is warm (uses the gap-driven `ConstraintRetriever`).
- Stage-gating LLMs must not call tools; the coordinator handles all tool IO in background tasks.
- Intent classification and natural-language interpretation must use LLMs (AutoGen agents) or explicit Slack slash commands; do not use regex/keyword matching.
- Plan in block-based terms (deep/shallow blocks, energy windows); time estimates are optional.
- Each stage agent has a single responsibility and a typed input/output contract; avoid prompt overlap.
- The coordinator is the only place that assembles context (facts + constraints + immovables) and passes it forward.

## Invariants

- Keep orchestration constants out of `agent.py`; use `constants.py` (timeouts/limits/fallbacks).
- Keep parsing/validation DRY; use `pydantic_parsing.py` helpers for LLM outputs and mixed payloads.
- Prefer Pydantic validation for Slack/MCP/Notion payloads; avoid try/except parsing and manual dict probing.
- Legacy/back-compat code must be marked with `# TODO(refactor):` and removed after migration.
- Keep MCP wiring out of `agent.py`; use `mcp_clients.py` for calendar/constraint-memory clients.
- Durable constraint retrieval is centralized in `constraint_retriever.py` (query_types -> type_ids -> query_constraints).
- Inject list-shaped prompt data via TOON tables (not JSON arrays); see `src/fateforger/llm/toon.py` and `toon_views.py`.

## Framework First (Don't Reinvent It)

- Prefer AutoGen capabilities for workflow control and routing:
  - `GraphFlow` / `DiGraphBuilder` for stage machines (see `flow_graph.py`, `nodes/nodes.py`).
  - Termination conditions (one user-facing message per Slack turn).
  - Typed outputs via `output_content_type` where the schema has no `oneOf` / discriminated unions.
  - Tools via `FunctionTool` / MCP clients (tool IO stays in the coordinator).
- Prefer structured message types over bespoke dict protocols (Pydantic models + `StructuredMessage`).

## Forbidden: Deterministic NLU

- Do not add deterministic extraction/interpretation of user intent from free-form text (scope/date/intent classification).
  - Example anti-pattern: `_infer_explicit_constraint_scope`-style keyword scans.
- Use multilingual structured LLM outputs instead:
  - `nlu.py` (`PlannedDateResult`, `ConstraintInterpretation`).
- Deterministic parsing is only acceptable for explicitly structured values (ISO timestamps, Slack IDs, known schema fields).

## LLM-Facing Models (tb_models.py, tb_ops.py)

- `TBEvent` / `TBPlan` are the **sole LLM-facing models** for timebox generation.
- `CalendarEvent` (SQLModel) stays for DB persistence + Slack display; never pass it to an LLM.
- All event types use the compact `ET` enum (`M`, `C`, `DW`, `SW`, `PR`, `H`, `R`, `BU`, `BG`).
- Timing is a discriminated union on field `a`: `ap` (after_previous), `bn` (before_next), `fs` (fixed_start), `fw` (fixed_window).
- `TBPatch` uses typed domain ops (`ae`, `re`, `ue`, `me`, `ra`) — never generic JSON Patch.
- `apply_tb_ops()` is the deterministic applicator; the LLM never directly mutates state.

## Sync Engine (sync_engine.py, submitter.py)

- Uses DeepDiff for semantic change detection (summary, start, end, description, colorId).
- Only mutates **agent-owned events** (identified by `fftb*` event ID prefix).
- Foreign calendar events are read-only FixedWindow constraints.
- Every remote op is logged in a `SyncTransaction` with `before_payload` for undo.
- Sync flow: `fetch_remote -> plan_sync(R, D) -> execute_sync -> log transaction`.
- Undo flow: `load transaction -> apply compensating ops in reverse`.
- `CalendarSubmitter` wraps the sync engine for coordinator use (`submit_plan()`, `undo_last()`).

## Patcher (patching.py)

- Uses AutoGen `AssistantAgent` with **schema-in-system-prompt** pattern.
- `TBPatch.model_json_schema()` is injected into the system prompt; the LLM returns raw JSON text.
- `_extract_patch()` strips markdown fences and parses the JSON.
- `output_content_type=TBPatch` is intentionally **NOT** used because `oneOf` from Pydantic discriminated unions breaks both OpenAI `response_format` and OpenRouter/Gemini structured output.
- **No trustcall** in the patching path.
- Patcher takes current `TBPlan` + user message + constraints -> returns `TBPatch`.
- `apply_tb_ops()` applies the patch deterministically.

## Background Work

- Local constraint extraction + persistence should run in background tasks.
- Durable (Notion) preference upserts should be fire-and-forget with dedupe + timeout.
- Durable constraint reads should run in the background and be merged with session-scoped constraints.
- Use a separate LLM client for background extraction/intent so it cannot block stage responses.
- MCP tool names are sanitized to OpenAI-safe versions (e.g., `constraint_query_constraints`).
- Only await pending background tasks if a downstream step strictly needs them (use short timeouts).
- If skeleton drafting times out, fall back to a minimal timebox so the flow keeps moving.
- Calendar meetings are treated as immovables (fixed start/end) and must be included before gap-filling.

## Stage Parallelism

- Stage 0: background-kick calendar prefetch + constraint retrieval (existing).
- Stage 2: **pre-generate skeleton** in background (assumes user proceeds) using immovables + constraints + inputs-so-far.
- Stage 3: use pre-generated skeleton if available; else draft synchronously.
- Stage 4: LLM -> `TBPatch` -> `apply_tb_ops()`.
- Stage 5: `plan_sync()` -> `execute_sync()` -> `SyncTransaction` logged.

## UX Status

- When background work is queued, include a short, friendly status note in stage responses.
- Status notes should reassure the user they can continue without waiting.

## Task Sources

- If TickTick MCP is configured (`TICKTICK_MCP_URL`), stage agents may use TickTick tools to pull tasks.
- Treat task fetch failures as non-blocking; continue the flow with user-provided inputs.

## Implementation Ticket

- **Read `TICKET_SYNC_ENGINE.md` (repo root) before making changes to this module.**
- Follow the phased checklist; update checkboxes as items complete.
