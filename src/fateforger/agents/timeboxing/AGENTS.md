# Timeboxing Agent Notes

## Goals
- Keep the timeboxing flow responsive; never block user replies on durable preference writes.
- Extract session-scoped constraints from user replies (not from generic "start timeboxing" requests).
- Prefetch durable constraints from Notion (via constraint-memory MCP) before Stage 1 so the cache is warm (uses the gap-driven `ConstraintRetriever`).
- Stage-gating LLMs must not call tools; the coordinator handles all tool IO in background tasks.
- Intent classification and natural-language interpretation must use LLMs (AutoGen agents) or explicit Slack slash commands; do not use regex/keyword matching.
- Plan in block-based terms (deep/shallow blocks, energy windows); time estimates are optional.
- Each stage agent has a single responsibility and a typed input/output contract; avoid prompt overlap.
- The coordinator is the only place that assembles context (facts + constraints + immovables) and passes it forward.
- Keep orchestration constants out of `agent.py`; use `constants.py` (timeouts/limits/fallbacks).
- Keep parsing/validation DRY; use `pydantic_parsing.py` helpers for LLM outputs and mixed payloads.
- Prefer Pydantic validation for Slack/MCP/Notion payloads; avoid try/except parsing and manual dict probing.
- Legacy/back-compat code must be marked with `# TODO(refactor):` and removed after migration.
- Keep MCP wiring out of `agent.py`; use `mcp_clients.py` for calendar/constraint-memory clients.
- Durable constraint retrieval is centralized in `constraint_retriever.py` (query_types → type_ids → query_constraints).
- Inject list-shaped prompt data via TOON tables (not JSON arrays); see `src/fateforger/llm/toon.py` and `src/fateforger/agents/timeboxing/toon_views.py`.

## Framework First (Don’t Reinvent It)
- Prefer AutoGen capabilities for workflow control and routing:
  - GraphFlow/`DiGraphBuilder` for stage machines (see `flow_graph.py`, `nodes/nodes.py`)
  - termination conditions (one user-facing message per Slack turn)
  - typed outputs via `output_content_type` (Pydantic)
  - tools via `FunctionTool` / MCP clients (tool IO stays in the coordinator)
- Prefer structured message types over bespoke dict protocols (Pydantic models + `StructuredMessage`).

## Forbidden: Deterministic “NLU”
- Do not add deterministic extraction/interpretation of user intent from free-form text (scope/date/intent classification).
  - Example anti-pattern: `_infer_explicit_constraint_scope`-style keyword scans.
- Use multilingual structured LLM outputs instead:
  - `src/fateforger/agents/timeboxing/nlu.py` (`PlannedDateResult`, `ConstraintInterpretation`)
- Deterministic parsing is only acceptable for explicitly structured values (ISO timestamps, Slack IDs, known schema fields).

## Background Work
- Local constraint extraction + persistence should run in background tasks.
- Durable (Notion) preference upserts should be fire-and-forget with dedupe + timeout.
- Durable constraint reads should run in the background and be merged with session-scoped constraints.
- Use a separate LLM client for background extraction/intent so it cannot block stage responses.
- MCP tool names are sanitized to OpenAI-safe versions (e.g., `constraint_query_constraints`).
- Only await pending background tasks if a downstream step strictly needs them (use short timeouts).
- If skeleton drafting times out, fall back to a minimal timebox so the flow keeps moving.
- Calendar meetings are treated as immovables (fixed start/end) and must be included before gap-filling.

## UX Status
- When background work is queued, include a short, friendly status note in stage responses.
- Status notes should reassure the user they can continue without waiting.

## Task Sources
- If TickTick MCP is configured (`TICKTICK_MCP_URL`), stage agents may use TickTick tools to pull tasks.
- Treat task fetch failures as non-blocking; continue the flow with user-provided inputs.
