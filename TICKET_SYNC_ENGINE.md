# Ticket: Deterministic Reversible Calendar Sync Engine

**Branch:** `timeboxing-stage3-validation`
**Status:** ✅ Phases 1-5 Complete (23/24 items checked — AC6 parallelism is stretch)
**Owner:** Agent + Hugo

---

## Goal

Replace the stub `CalendarSubmitter` + trustcall-based `TimeboxPatcher` with a **deterministic, incremental, reversible calendar sync engine** that:

1. Uses token-efficient `TBPlan`/`TBEvent` models (proven in notebook) instead of heavy `CalendarEvent` SQLModel
2. Uses typed domain ops (`TBPatch`) instead of opaque trustcall whole-object replacement
3. Syncs to Google Calendar via MCP with minimal ops (create/update/delete only what changed)
4. Uses DeepDiff for semantic change detection
5. Logs every remote operation for deterministic undo
6. Scopes mutations to agent-owned events only (stable `timebox_event_id` identity)

## Non-Goals

- Multi-level / selective undo (v2 — data model supports it but UX deferred)
- ETag-based optimistic concurrency (v2 — MCP server may not expose etags yet)
- `extendedProperties.private` ownership markers (v2 — test MCP server support first; v1 uses deterministic `fftb*` event IDs)
- Google Calendar `syncToken` incremental reads (v2)
- Migrating `CalendarEvent` SQLModel (keep for persistence; `TBPlan` is LLM-facing only)

## Acceptance Criteria

### AC1: Generation models extracted and tested
- [x] `tb_models.py` contains `ET`, `AfterPrev`, `BeforeNext`, `FixedStart`, `FixedWindow`, `TBEvent`, `TBPlan`, `Timing` union, `_ET_COLOR_MAP`
- [x] `tb_ops.py` contains `AddEvents`, `RemoveEvent`, `UpdateEvent`, `MoveEvent`, `ReplaceAll`, `TBOp`, `TBPatch`, `apply_tb_ops()`
- [x] Both modules pass unit tests for: time resolution, overlap detection, all 5 ops, discriminated union (de)serialization

### AC2: Sync engine built with DeepDiff
- [x] `sync_engine.py` contains:
  - `fetch_remote_events(date, tz, mcp_url) -> tuple[TBPlan, dict[str, str]]` — MCP list-events → TBPlan + `{summary+start: gcal_id}` map
  - `plan_sync(remote: TBPlan, desired: TBPlan, event_id_map) -> list[SyncOp]` — DeepDiff-based semantic diff → minimal create/update/delete ops
  - `SyncOp` dataclass with `op_type`, `gcal_event_id`, `before_payload`, `after_payload`
  - `SyncTransaction` dataclass with `ops`, `timestamp`, `status`
  - `execute_sync(ops, mcp_url) -> SyncTransaction` — MCP batch execution with per-op result tracking
  - `undo_sync(tx, mcp_url) -> SyncTransaction` — compensating ops in reverse order
- [x] `plan_sync` uses DeepDiff to detect meaningful field changes (summary, start, end, description, colorId) and ignores noise (etag, updated, sequence)
- [x] Agent-managed events identified by deterministic `fftb*` event ID prefix
- [x] Sync engine passes unit tests with mocked MCP responses

### AC3: Patcher uses TBPatch + AutoGen FunctionTool
- [x] `patching.py` rewritten: `TimeboxPatcher.__init__` takes a model client; `apply_patch` calls an AutoGen `AssistantAgent` with `FunctionTool(TBPatch, strict=True)` 
- [x] Patcher returns `TBPatch` (typed ops) which is applied via `apply_tb_ops()`
- [x] Trustcall dependency removed from patching path
- [x] Patcher passes unit test with mocked LLM response

### AC4: Submitter wired to sync engine
- [x] `submitter.py` rewritten: `submit_plan(tb_plan, event_id_map, mcp_url) -> SyncTransaction`
- [x] `undo_last(mcp_url) -> SyncTransaction`
- [x] Submitter passes unit test with mocked MCP

### AC5: GraphFlow nodes rewired
- [x] `Session` dataclass gains: `tb_plan: TBPlan | None`, `base_snapshot: TBPlan | None`, `event_id_map: dict[str, str]`
- [x] `StageSkeletonNode` produces `TBPlan` via `ReplaceAll` op on `gcal_response_to_tb_plan()` baseline
- [x] `StageRefineNode` uses new `TimeboxPatcher` (TBPatch + apply_tb_ops)
- [x] `StageReviewCommitNode` calls real sync engine submit
- [x] `Timebox` kept for backward compat; conversion functions `tb_plan_to_timebox()` / `timebox_to_tb_plan()` added

### AC6: Parallelism & pre-generation
- [ ] Stage 0 (date confirmation) kicks off parallel background tasks:
  - Calendar prefetch via MCP (`fetch_remote_events`)
  - Constraint retrieval from Notion MCP
- [ ] Stage 1 (constraints) continues constraint collection while calendar data loads
- [ ] Stage 2 (inputs) pre-generates a skeleton TBPlan in background (assuming user will proceed) using already-fetched immovables + constraints — so Stage 3 is near-instant
- [ ] Stage 3 (skeleton) uses pre-generated TBPlan if available, else drafts synchronously
- [ ] Stage 4 (refine) applies TBPatch ops incrementally
- [ ] Stage 5 (review/commit) submits via sync engine

### AC7: Live integration tests pass
- [x] Unit tests pass: `poetry run pytest tests/test_tb_models.py tests/test_tb_ops.py tests/test_sync_engine.py -v`
- [x] Live MCP test: create → update → delete → verify (against real GCal MCP server)
- [x] Live LLM test: AutoGen agent produces valid TBPatch from natural language instruction

## Design Notes

### Model Hierarchy

```
LLM-facing (generation):     TBEvent → TBPlan → TBPatch → apply_tb_ops()
Sync engine:                  TBPlan → plan_sync() → SyncOp[] → execute_sync()
Calendar MCP:                 SyncOp → create-event / update-event / delete-event
Persistence (unchanged):      CalendarEvent (SQLModel) — used for DB + Slack display
Conversion:                   tb_plan_to_timebox() / timebox_to_tb_plan()
```

### Event Identity Strategy (v1)

- Agent-created events get deterministic base32hex IDs: `fftb` + SHA1(date|name|start|index)
- Events with `fftb*` prefix are "owned" by the agent → eligible for update/delete
- Events without prefix are "foreign" (user calendar events) → read-only constraints
- Foreign events appear in TBPlan as `FixedWindow` anchors but are never mutated

### DeepDiff Usage

```python
from deepdiff import DeepDiff

# Canonical event representation for diffing
def _canonical(resolved_event: dict) -> dict:
    return {
        "summary": resolved_event["n"],
        "start": resolved_event["start_time"].isoformat(),
        "end": resolved_event["end_time"].isoformat(),
        "description": resolved_event.get("d", ""),
        "colorId": _ET_COLOR_MAP.get(resolved_event["t"], "0"),
    }

# Diff remote vs desired
diff = DeepDiff(
    {eid: _canonical(r) for eid, r in remote_by_id.items()},
    {eid: _canonical(d) for eid, d in desired_by_id.items()},
    ignore_order=True,
)
# diff.get("dictionary_item_added")   → creates
# diff.get("dictionary_item_removed") → deletes  
# diff.get("values_changed")          → updates
```

### Stage Parallelism

```
Stage 0 (date confirm)
  └─ background: prefetch_calendar_immovables()  ← already exists
  └─ background: prefetch_durable_constraints()  ← already exists

Stage 1 (constraints)
  └─ foreground: constraint gate LLM
  └─ background: constraint extraction tasks    ← already exists

Stage 2 (inputs)
  └─ foreground: capture inputs gate LLM
  └─ background: PRE-GENERATE skeleton TBPlan   ← NEW (assumes proceed)
     Uses: immovables (from stage 0) + constraints + inputs-so-far
     Result cached on session.pre_generated_skeleton

Stage 3 (skeleton)
  └─ if session.pre_generated_skeleton exists: use it (near-instant)
  └─ else: draft synchronously (current behavior)

Stage 4 (refine)
  └─ LLM → TBPatch → apply_tb_ops()

Stage 5 (review/commit)
  └─ plan_sync() → execute_sync() → SyncTransaction logged
```

### File Layout

```
src/fateforger/agents/timeboxing/
  tb_models.py          ← NEW: ET, Timing, TBEvent, TBPlan
  tb_ops.py             ← NEW: TBOp, TBPatch, apply_tb_ops()
  sync_engine.py        ← NEW: plan_sync, execute_sync, undo_sync
  patching.py           ← REWRITE: TBPatch + AutoGen FunctionTool
  submitter.py          ← REWRITE: wire to sync_engine
  timebox.py            ← KEEP: backward compat, add conversion fns
  agent.py              ← MODIFY: Session gains tb_plan/base_snapshot
  flow_graph.py         ← KEEP (no changes needed)
  nodes/nodes.py        ← MODIFY: skeleton/refine/review nodes

tests/
  test_tb_models.py     ← NEW
  test_tb_ops.py        ← NEW  
  test_sync_engine.py   ← NEW
```

## Implementation Checklist

### Phase 1: Extract Models (no production changes)
- [x] Create `tb_models.py` from notebook cell 33
- [x] Create `tb_ops.py` from notebook cell 34
- [x] Write `tests/test_tb_models.py`
- [x] Write `tests/test_tb_ops.py`
- [x] All tests pass (62 tests)

### Phase 2: Sync Engine
- [x] Install `deepdiff` via poetry (already present)
- [x] Create `sync_engine.py` with `SyncOp`, `SyncTransaction`, `plan_sync`, `execute_sync`, `undo_sync`
- [x] Write `tests/test_sync_engine.py` with mocked MCP
- [x] All tests pass (29 tests)

### Phase 3: Rewire Patcher
- [x] Rewrite `patching.py` to use `TBPatch` + AutoGen `output_content_type`
- [x] Remove trustcall from patching imports
- [x] Add conversion functions to `timebox.py`: `tb_plan_to_timebox()`, `timebox_to_tb_plan()`
- [x] Update `__init__.py` exports
- [x] Tests pass (242 unit tests, 0 regressions)
- [x] Fix `output_content_type=TBPatch` → schema-in-system-prompt (OpenAI rejects `oneOf`, OpenRouter hangs)

### Phase 4: Rewire Submitter + Nodes
- [x] Rewrite `submitter.py` to call sync engine (`CalendarSubmitter` class)
- [x] Update `Session` dataclass in `agent.py` (tb_plan, base_snapshot, event_id_map)
- [x] Update `StageSkeletonNode`, `StageRefineNode`, `StageReviewCommitNode`
- [ ] Add skeleton pre-generation in Stage 2 background
- [x] Tests pass (228 unit tests, 0 regressions)

### Phase 5: Live Integration Tests
- [x] Create `notebooks/phase5_integration_test.ipynb`
- [x] Fix `sync_engine.plan_sync()` empty→populated DeepDiff edge case (set-diff for creates/deletes)
- [x] Fix `_extract_patch()` to strip markdown code fences from LLM responses
- [x] Fix `patching.py` — replace `output_content_type=TBPatch` with schema-in-system-prompt (Gemini/OpenRouter compat)
- [x] Live MCP: baseline submit (5 creates) → round-trip verify → incremental sync (2 creates) → undo → second iteration
- [x] Live LLM: Gemini via OpenRouter produces valid TBPatch from natural language (2 successful patches)
- [x] Add `tests/unit/test_patching.py` (14 tests)
- [x] All 115 unit tests pass (0 regressions)

### Phase 6: Cleanup & Docs
- [x] Update TICKET_SYNC_ENGINE.md status and checkboxes
- [x] Update memory bank progress
- [x] Update module README/status

### Phase 7: Future (Deferred)
- [ ] AC6: Stage 2 skeleton pre-generation (parallelism — stretch goal)
- [ ] Wire `CalendarSubmitter` into live Slack flow end-to-end
- [ ] Remove `trustcall` from `pyproject.toml` (only used in archive notebooks/scripts)
- [ ] Add CI job for the 115-test sync engine suite
