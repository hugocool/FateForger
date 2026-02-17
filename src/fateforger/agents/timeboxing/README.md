# Timeboxing Agent

Stage-gated timeboxing workflow that builds daily schedules via conversational refinement and syncs to Google Calendar.

## Status

| Subsystem | Status | Tests | Confirmed |
|-----------|--------|-------|-----------|
| Domain models (tb_models, tb_ops) | Implemented, Tested | 62 unit | 2025-07-22 |
| Sync engine (sync_engine, submitter) | Implemented, Tested | 29 + 10 unit | 2025-07-22 (live MCP) |
| Patching (schema-in-prompt) | Implemented, Tested | 14 unit | 2025-07-22 (live LLM) |
| GraphFlow orchestration | Implemented, Documented | graphflow state machine tests | — |
| Skeleton pre-generation (AC1) | Implemented, Tested | `test_timeboxing_skeleton_pre_generation.py` | — |
| Calendar sync + undo controls | Implemented, Tested | `test_timeboxing_submit_flow.py`, `test_slack_timebox_buttons.py` | — |
| Stage 1 constraint-template coverage UX | Implemented, Tested | `test_timeboxing_stage_message_template_coverage.py` | — |
| Durable profile/date-span constraint auto-upsert + Stage 1 prefetch wait | Implemented, Tested | `test_timeboxing_durable_constraints.py`, `test_timeboxing_constraint_memory_client_tool_name.py` | — |
| Stage 3 markdown-first skeleton overview | Implemented, Tested | `test_timeboxing_skeleton_draft_contract.py` | — |
| Stage 4 advisory quality facts (0-4) | Implemented, Tested | `test_phase4_rewiring.py` | — |
| Deterministic stage action buttons | Implemented, Tested | `test_timeboxing_stage_actions.py`, `test_slack_timebox_stage_buttons.py` | — |
| Structured-output strict tool contract | Implemented, Tested | `test_timeboxing_constraint_search_tool_strict.py`, `test_timeboxing_flow.py` | — |

## File Index

### Orchestration

| File | Responsibility |
|------|---------------|
| `agent.py` | `PlanningCoordinator`: owns Session, routes Slack messages, runs background tasks, manages stage transitions. Entry points: `on_start()`, `on_commit_date()`, `on_user_reply()`. |
| `flow_graph.py` | `build_timeboxing_graphflow()`: constructs the AutoGen GraphFlow DAG. Single source of truth for stage transitions and edge conditions. |
| `stage_gating.py` | `TimeboxingStage` enum, `StageGateOutput` model, LLM prompt templates for each stage gate. |
| `contracts.py` | Typed stage-context contracts (`SkeletonContext`, `ConstraintContext`, etc.): what each stage receives as input. |
| `constants.py` | Orchestration timeouts, limits, and fallback values. No magic numbers. |

### Domain Models (LLM-Facing)

| File | Responsibility |
|------|---------------|
| `tb_models.py` | `ET` (event type enum), `TBEvent`, `TBPlan`, `Timing` union (`AfterPrev`, `BeforeNext`, `FixedStart`, `FixedWindow`), `_ET_COLOR_MAP`. Calendar-native, sync-friendly. |
| `tb_ops.py` | `TBPatch`, `TBOp` union (`AddEvents`, `RemoveEvent`, `UpdateEvent`, `MoveEvent`, `ReplaceAll`), `apply_tb_ops()`. Pure-function ops engine: deterministic plan mutation. |
| `timebox.py` | Legacy `Timebox` schema + `schedule_and_validate()`. Conversion: `timebox_to_tb_plan()`, `tb_plan_to_timebox()`. Kept for backward compat with Stage 3 drafting and Slack display. |

### Calendar Sync

| File | Responsibility |
|------|---------------|
| `sync_engine.py` | `plan_sync()`, `execute_sync()`, `undo_sync()`, `gcal_response_to_tb_plan()`. Deterministic, incremental, reversible diff-and-apply via MCP. Uses set-diff for creates/deletes, DeepDiff for updates. |
| `submitter.py` | `CalendarSubmitter`: high-level `submit_plan()`, `undo_last()`, and `undo_transaction()` over the sync engine. |
| `mcp_clients.py` | `McpCalendarClient` (list/create/update/delete events via MCP), `McpConstraintMemoryClient` (Notion constraint MCP). Internal to coordinator. |

### LLM Patching

| File | Responsibility |
|------|---------------|
| `patching.py` | `TimeboxPatcher`: sends `TBPlan` + user feedback to Gemini via `AssistantAgent`. Injects `TBPatch` JSON schema into system prompt (not `output_content_type`, which breaks on `oneOf`). `_extract_patch()` strips markdown fences. |

### Prompt Engineering

| File | Responsibility |
|------|---------------|
| `skeleton_draft_system_prompt.j2` | Jinja2 template for skeleton drafting (consumes TOON tables). |
| `prompt_rendering.py` | `render_skeleton_draft_system_prompt()`: Jinja renderer. |
| `planning_policy.py` | Shared Stage 3/4 planning policy text + quality rubric constants. |
| `toon_views.py` | Timeboxing-specific TOON table views (minimal columns for events, constraints, tasks). |
| `prompts.py` | Legacy prompt strings (being migrated to `stage_gating.py`). |

### NLU and Constraints

| File | Responsibility |
|------|---------------|
| `nlu.py` | `PlannedDateResult`, `ConstraintInterpretation`: structured LLM outputs for multilingual date/scope inference. No regex/keyword matching. |
| `preferences.py` | `ConstraintStore`: SQLite-backed session constraint persistence. |
| `constraint_retriever.py` | `ConstraintRetriever`: gap-driven durable constraint fetch from Notion MCP. |
| `constraint_search_tool.py` | Stage-gating Notion search tool (`search_constraints`) with strict FunctionTool schema for structured-output compatibility. |
| `notion_constraint_extractor.py` | LLM fallback path for durable Notion upserts when deterministic MCP upsert mapping is unavailable. |

### Utilities

| File | Responsibility |
|------|---------------|
| `pydantic_parsing.py` | Tolerant parsing helpers for LLM outputs and mixed payloads. |
| `messages.py` | `StartTimeboxing`, `TimeboxingUserReply`, `TimeboxingCommitDate`: typed Slack-to-agent routing messages. |
| `actions.py` | Slack action/button payload models and helpers (planning cards). |
| `state.py` | Session persistence helpers. |
| `flow.py` | Legacy flow logic (being replaced by GraphFlow). |

### Subfolders

| Folder | Responsibility |
|--------|---------------|
| `nodes/` | GraphFlow node agents (TurnInit, Decision, Transition, Stage nodes, Presenter). See `nodes/README.md`. |

## Architecture

### Coordinator + Stage Agents

- **Coordinator** (`agent.py`): owns Session state, merges facts/constraints, runs background tool work (calendar, Notion), and decides which stage runs next.
- **Stage agents** (in `nodes/`): pure functions over typed JSON input returning typed JSON output. No direct tool IO.
- **GraphFlow** (`flow_graph.py`): runs the stage machine as a directed graph; transitions are testable and explicit.

### Stage Pipeline

```
Stage 0: Date Confirmation (Slack buttons)
    background: calendar prefetch + Notion constraint retrieval (with short await before first Stage 1 render)
Stage 1: CollectConstraints -> StageGateOutput (frame_facts)
Stage 2: CaptureInputs -> StageGateOutput (input_facts)
Stage 3: Skeleton -> pre-generated draft if available, else synchronous draft -> markdown overview (presentation-first) + carry-forward seed `TBPlan` (no Stage 3 patch loop)
Stage 4: Refine -> ensure TBPlan+baseline -> TBPatch -> apply_tb_ops() -> updated TBPlan -> advisory quality facts (0-4) -> sync to Google Calendar
Stage 5: ReviewCommit -> final summary (no extra submit gate)
Undo action: undo latest sync transaction via session-backed state -> return to Refine
Each stage response also includes deterministic Slack actions (Proceed when ready, plus Back/Redo/Cancel) that replace the same message when clicked.
```

### Session State

Session dataclass lives in `agent.py`. Core fields:

| Field | Purpose |
|-------|---------|
| `thread_ts`, `channel_id`, `user_id` | Slack anchors |
| `frame_facts`, `input_facts` | Accumulated LLM outputs per stage |
| `timebox` | Legacy Timebox (Stage 3+) |
| `tb_plan` | Current TBPlan, sync-engine model (prepared by Stage 2 draft and/or Stage 4 preflight) |
| `base_snapshot` | Remote-baseline snapshot for diff-based sync (prepared in Stage 4 preflight) |
| `event_id_map` | Dict mapping event key to GCal event ID |
| `pre_generated_skeleton` | Background Stage 2 draft consumed by Stage 3 when fresh |
| `skeleton_overview_markdown` | Stage 3 markdown summary rendered to Slack |
| `last_quality_level`, `last_quality_label`, `last_quality_next_step` | Latest Refine quality snapshot carried into next patch context |
| `last_sync_transaction` | Session-backed transaction used for deterministic undo |
| `active_constraints` | Merged constraint state |
| `stage` | Current TimeboxingStage enum |
| `graphflow` | Per-session GraphFlow instance |

### Model Hierarchy

```
LLM-facing:      TBEvent -> TBPlan -> TBPatch -> apply_tb_ops()
Sync engine:     TBPlan -> plan_sync() -> SyncOp[] -> execute_sync()
Calendar MCP:    SyncOp -> create-event / update-event / delete-event
Persistence:     CalendarEvent (SQLModel) for DB + Slack display
Conversion:      timebox_to_tb_plan() / tb_plan_to_timebox()
```

### Event Identity

- Agent-created events get deterministic base32hex IDs: `fftb` + SHA1(date|name|start|index).
- `fftb*` prefix = owned, eligible for update/delete.
- No prefix = foreign (user calendar), read-only FixedWindow constraints.

### Patching (Schema-in-Prompt)

`output_content_type=TBPatch` is intentionally NOT used because OpenAI `response_format` rejects `oneOf` from Pydantic discriminated unions and OpenRouter/Gemini hangs with structured output on complex schemas. Instead: inject `TBPatch.model_json_schema()` into the system prompt and parse the raw JSON text response.

Runtime guard: `TimeboxPatcher.apply_patch(...)` is Refine-only (`stage='Refine'`) and rejects any non-Refine invocation.

### TOON Prompt Injection

List-shaped data (constraints, tasks, immovables, events) uses TOON tabular format, not JSON arrays. Encoder: `src/fateforger/llm/toon.py`.

## Related Files (Outside This Folder)

| File | Role |
|------|------|
| `src/fateforger/slack_bot/handlers.py` | Routes Slack events to coordinator |
| `src/fateforger/slack_bot/timeboxing_commit.py` | Stage 0 Slack UI (day picker + confirm button) |
| `src/fateforger/llm/toon.py` | TOON tabular encoder |
| `TICKET_SYNC_ENGINE.md` | Implementation ticket (repo root) |
| `notebooks/phase5_integration_test.ipynb` | Live MCP + LLM integration tests |

## How to Run Tests

```bash
# Sync engine suite (115 tests)
poetry run pytest tests/unit/test_tb_models.py tests/unit/test_tb_ops.py \
  tests/unit/test_sync_engine.py tests/unit/test_phase4_rewiring.py \
  tests/unit/test_patching.py -v

# GraphFlow state machine
poetry run pytest tests/unit/test_timeboxing_graphflow_state_machine.py -v

# All timeboxing-related tests
poetry run pytest tests/unit/ -k timeboxing -v
```
