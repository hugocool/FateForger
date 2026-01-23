# Agent Intent / Determinism Audit

This is a quick repo audit of **agents** for:

1) how *intent classification* is performed, and  
2) whether there is any **deterministic (non‑LLM) extraction/interpretation** of user free‑form text.

Policy baseline:

- Intent classification and natural-language interpretation must use **LLMs (AutoGen agents)** or **explicit slash commands**.
- Deterministic parsing is only acceptable for **explicitly structured inputs** (ISO timestamps, Slack IDs, MCP payload shapes, known schema fields).

Scope of audit:

- `src/fateforger/agents/**` (agent modules only)

## Findings (by agent)

### `ReceptionistAgent` — `src/fateforger/agents/receptionist/agent.py`

- **Intent classification:** LLM-only via AutoGen handoff tools (good).
- **Determinism present (non-NLU):**
  - `_format_llm_failure(...)` does substring matching on provider error strings (e.g., 401/429/timeout) to produce a friendlier error message.
  - `_follow_up_plan(...)` uses a simple `?` check to choose a follow-up delay.
- **Deterministic NLU from user free-form text:** none.

### `AdmonisherAgent` — `src/fateforger/agents/admonisher/agent.py`

- **Intent classification:** LLM-only via handoff tools (good).
- **Deterministic NLU from user free-form text:** none.
- **Determinism present:** none (beyond standard message plumbing).

### `PlannerAgent` (calendar) — `src/fateforger/agents/schedular/agent.py`

- **Intent classification:** not applicable; most entry points are typed messages (`SuggestNextSlot`, `UpsertCalendarEvent`, etc.).
- **Determinism present (structured parsing / algorithms):**
  - Parses calendar event timestamps from MCP payloads via `dateutil.parser.isoparse` (structured).
  - Computes busy intervals + gaps deterministically to suggest a slot (algorithmic planning, not NLU).
  - `_follow_up_plan(...)` uses a `?` check (same as receptionist).
  - Filters canceled events via `event["status"].lower() == "cancelled"` (structured field).
- **Deterministic NLU from user free-form text:** none.

### `RevisorAgent` — `src/fateforger/agents/revisor/agent.py`

- **Intent classification:** not a router; it is a specialist agent.
- **Deterministic NLU from user free-form text:** none.
- **Determinism present:** none.

### `TasksAgent` — `src/fateforger/agents/tasks/agent.py`

- **Intent classification:** not a router; it is a specialist agent.
- **Deterministic NLU from user free-form text:** none.
- **Determinism present:** optional deterministic TickTick MCP client/tool loading based on env config (not NLU).

### `TimeboxingFlowAgent` — `src/fateforger/agents/timeboxing/agent.py`

- **Intent classification / natural language interpretation:**
  - Planned date interpretation: **LLM structured output** (`nlu.py::PlannedDateResult`).
  - Constraint interpretation (intent + scope + extraction decision): **LLM structured output** (`nlu.py::ConstraintInterpretation`).
- **Determinism present (structured parsing / safety):**
  - `date.fromisoformat(...)` for ISO inputs (structured).
  - database URL scheme checks (`startswith("sqlite://")`) (config parsing).
  - enum coercion via `Enum(str(value).lower())` (structured values).
  - calendar event status filter `== "cancelled"` via structured field.
- **Deterministic NLU from user free-form text:** none (by design).

### `task_marshal` — `src/fateforger/agents/task_marshal/agent.py`

- File contains only imports; no behavior implemented yet.

## Cross-cutting deterministic parsing (acceptable)

These patterns show up and are considered acceptable because they operate on **structured data**:

- `dateutil.parser.isoparse(...)`, `datetime.fromisoformat(...)`, `date.fromisoformat(...)`, `time.fromisoformat(...)`
- checking for dict keys in MCP payloads (`"id" in payload`, `"items" in payload`, etc.)
- checking structured fields (`event["status"] == "cancelled"`)
- DB URL scheme checks (`startswith("sqlite://")`)

## Potential policy risks to watch

- Substring checks on *user* messages for “routing” (none found in `src/fateforger/agents/**` at audit time).
- Keyword lists / heuristics for scope or date inference (removed from timeboxing; none found elsewhere in agents).

## Recommended guardrails

- Keep deterministic NLU out of agent code. If interpretation is needed:
  - use structured LLM outputs (`output_content_type=...`) and centralize prompts/DTOs in a dedicated module (e.g., `timeboxing/nlu.py`).
- Prefer AutoGen workflow primitives for orchestration/routing:
  - GraphFlow/`DiGraphBuilder`, termination conditions, message filtering, typed outputs, `FunctionTool`.

