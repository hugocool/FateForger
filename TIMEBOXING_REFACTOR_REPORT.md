# Timeboxing Refactor Report

This file documents the recent timeboxing refactor work, with pointers to the relevant code and tests.

## Why this refactor happened

Primary goals:

- Remove prompt overlap (no “mega prompt” bleeding instructions across stages).
- Make stage handoffs explicit and typed (each stage gets a single JSON context object).
- Treat calendar meetings as **immovables** and fill gaps with DW/SW blocks.
- Run constraint extraction + durable preference upserts **in the background** (non-blocking).
- Keep Slack UX responsive (incremental updates + background status).
- Enforce: **Intent classification via LLMs or explicit slash commands only** (no regex/keyword intent).
- Enforce: **Never use Gemini < 3.0**.

## What changed (high level)

### 1) Stage contracts + coordinator-owned state

- Typed stage contexts live in `src/fateforger/agents/timeboxing/contracts.py`.
- `TimeboxingFlowAgent` (coordinator) now builds explicit JSON contexts per stage, and stage-gate agents receive only that JSON (no “Known facts…” prose wrapper):
  - `src/fateforger/agents/timeboxing/agent.py` (`_build_collect_constraints_context`, `_build_capture_inputs_context`, `_run_stage_gate`)

### 1b) GraphFlow stage machine (single implementation)

The stage machine runs as an AutoGen **GraphFlow** directed graph (instead of a hand-rolled `if/elif` stage dispatcher).
- Graph builder: `src/fateforger/agents/timeboxing/flow_graph.py`
- Node agents: `src/fateforger/agents/timeboxing/nodes/nodes.py`

Important GraphFlow nuance:

- `DiGraphBuilder.add_edge(...)` defaults to `activation_condition="all"` for nodes with multiple parents.
- Multi-parent nodes like `PresenterNode` must set `activation_condition="any"` on incoming edges, otherwise GraphFlow can terminate early without producing output.

### 2) Skeleton drafting prompt split (single-purpose template)

- Skeleton draft system prompt is now a small Jinja template:
  - `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2`
  - Rendered by `src/fateforger/agents/timeboxing/prompt_rendering.py`
  - Used in `src/fateforger/agents/timeboxing/agent.py` (`_run_skeleton_draft`)

### 2b) Prompt injection uses TOON tables (list data)

To keep prompts short and deterministic, list-shaped structured data is injected into prompts using TOON tabular format (rather than JSON arrays):

- Encoder: `src/fateforger/llm/toon.py` (`toon_encode`)
- Timeboxing-specific views: `src/fateforger/agents/timeboxing/toon_views.py`
- Skeleton prompt template now consumes TOON tables:
  - `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2`
  - rendered by `src/fateforger/agents/timeboxing/prompt_rendering.py`

Note: the `toon-format` dependency exists but its Python encoder is not implemented (`toon_format.encode()` raises `NotImplementedError`), so the repo uses a small internal encoder for now.

### 3) Explicit constraint + immovable injection

- Skeleton drafting no longer “hopes” constraints appear via free-form facts.
- Coordinator explicitly assembles a `SkeletonContext` and injects:
  - `constraints_snapshot` (durable + active)
  - `immovables` (calendar meetings)
- Implementation: `src/fateforger/agents/timeboxing/agent.py` (`_build_skeleton_context`)

### 3b) Multilingual natural parsing (structured LLM output)

Deterministic, English-only parsing for:

- planned date inference
- constraint scope inference

has been replaced with **structured LLM outputs** (Pydantic) so it works across languages and paraphrases:

- `src/fateforger/agents/timeboxing/nlu.py`
  - `PlannedDateResult`
  - `ConstraintInterpretation`

The constraint pipeline now collapses “intent + scope + extraction decision” into a single interpreter call per user message, which reduces LLM calls and avoids brittle keyword triggers.

### 4) Scheduling/validation robustness

`Timebox.schedule_and_validate` is the token-saver: the LLM can emit minimal events, and the validator fills times deterministically.

Fixes and improvements:

- Parse/normalize common LLM encodings (`"HH:MM"`, duration strings) before validating.
- Corrected anchor semantics and made overlap errors more actionable.

Implementation:

- `src/fateforger/agents/timeboxing/timebox.py`
- `src/fateforger/agents/schedular/models/calendar.py`

### 5) Slack `/timebox` wiring test stability

- Updated the `/timebox` e2e test to match the handler signature and avoid broken stubs:
  - `tests/e2e/test_slack_timebox_command.py`

### 6) Planning card regression fix (unrelated but test-blocking)

The planning card tests expect a `datetimepicker` “start_at” control.

- `src/fateforger/slack_bot/planning.py` restores `datetimepicker` (`FF_EVENT_START_AT_ACTION_ID`) in the interactive card.

### 7) Tool schema compatibility (OpenAI tool-name rules)

OpenAI-compatible tool schemas disallow `.` in tool names.

- The constraint-memory MCP server now exposes OpenAI-safe tool names (underscores only):
  - `scripts/constraint_mcp_server.py`
- Defensive tool name normalization (for any future MCP servers with non-compliant names) lives in:
  - `src/fateforger/tools/constraint_mcp.py`

### 8) Gemini model policy (>= 3.0 only)

Defaults are updated so OpenRouter “flash” defaults don’t fall back to Gemini 2.x.

- `src/fateforger/core/config.py`
- `src/fateforger/llm/factory.py`
- `.env.template`
- `src/fateforger/setup_wizard/templates/setup_env.html`

## Key file map (where to look)

Timeboxing coordinator + stages:

- `src/fateforger/agents/timeboxing/agent.py`
- `src/fateforger/agents/timeboxing/flow_graph.py`
- `src/fateforger/agents/timeboxing/nlu.py`
- `src/fateforger/agents/timeboxing/nodes/nodes.py`
- `src/fateforger/agents/timeboxing/stage_gating.py`
- `src/fateforger/agents/timeboxing/contracts.py`
- `src/fateforger/agents/timeboxing/timebox.py`
- `src/fateforger/agents/timeboxing/patching.py`

Skeleton prompt template:

- `src/fateforger/agents/timeboxing/skeleton_draft_system_prompt.j2`
- `src/fateforger/agents/timeboxing/prompt_rendering.py`

Slack entry points:

- `src/fateforger/slack_bot/handlers.py` (`/timebox` command handler + routing)
- `src/fateforger/slack_bot/timeboxing_commit.py` (commit UI)

Docs:

- `src/fateforger/agents/timeboxing/README.md`
- `src/fateforger/agents/timeboxing/AGENTS.md`
- `src/fateforger/slack_bot/README.md`
- `docs/indices/agents_timeboxing.md`

## Tests added/updated (how behavior is pinned)

- Scheduling semantics:
  - `tests/unit/test_timebox_schedule_and_validate.py`
- Skeleton context injection:
  - `tests/unit/test_timeboxing_skeleton_context_injection.py`
- TOON encoding + injection:
  - `tests/unit/test_toon_encode.py`
  - `tests/unit/test_timeboxing_prompt_rendering.py`
  - `tests/unit/test_timeboxing_stage_gate_json_context.py`
- Constraint MCP tool-name rules:
  - `tests/unit/test_constraint_mcp_tool_names.py`
- Durable constraint upsert tool behavior:
  - `tests/unit/test_timeboxing_constraint_extractor_tool_strict.py`
  - `tests/unit/test_timeboxing_constraint_extractor_tool_nonblocking.py`
- Slack `/timebox` command integration:
  - `tests/e2e/test_slack_timebox_command.py`

- GraphFlow state machine routing:
  - `tests/unit/test_timeboxing_graphflow_state_machine.py`

- Constraint pipeline (single structured interpretation call):
  - `tests/unit/test_timeboxing_constraint_extraction_background.py`

- Gap-driven durable constraint retrieval (type-routing via MCP):
  - `src/fateforger/agents/timeboxing/constraint_retriever.py`
  - `tests/unit/test_constraint_retriever.py`
  - `tests/integration/test_timeboxing_durable_constraint_retriever_wiring.py`
  - `tests/unit/test_timeboxing_durable_constraints.py`

## How to reproduce diffs locally

From repo root:

```bash
git diff --stat
git diff
```

To focus on timeboxing:

```bash
git diff -- src/fateforger/agents/timeboxing
```

## How to run

Tests:

```bash
.venv/bin/python -m pytest -q
```

Docs:

```bash
make docs-build
make docs-serve
```

## Remaining TODOs (if you want to push further)

- Add a “flow simulation” test that drives `TimeboxingFlowAgent` end-to-end with fully stubbed model clients (no network) and asserts stage transitions + Slack outputs.
- Add coverage tooling (e.g. `coverage` / `pytest-cov`) if you want numeric coverage enforcement.
