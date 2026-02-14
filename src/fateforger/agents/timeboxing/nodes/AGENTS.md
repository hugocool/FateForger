# Timeboxing Nodes — Agent Notes

**Scope:** Operational rules for `nodes/`. For file index and data flow, see `README.md` in this folder.

## Design Rules

- Each node is a `BaseChatAgent` with `on_messages()` / `on_messages_stream()`.
- Nodes are **stateless between turns**; all mutable state lives on the shared `Session` object passed at construction.
- Nodes must not import or call MCP clients, Slack SDK, or database layers. Tool IO is the coordinator's job.
- Nodes should not catch and swallow exceptions; let them propagate so the coordinator can handle them.
- One user-facing message per Slack turn; `PresenterNode` is the only node that produces user-visible output.
- No node may inspect LLM prose via keyword/substring/regex heuristics. Use typed outputs (`StageGateOutput`, `StageDecision`, etc.) and explicit session state only.

## GraphFlow Safety

- Every node must be registered in `flow_graph.py` via `DiGraphBuilder`; do not instantiate nodes outside the graph.
- Edge conditions in `flow_graph.py` are the single source of truth for stage transitions; do not hard-code transitions in nodes.
- If a node needs to signal "stay in current stage" vs "advance", it must do so via its return value (e.g., `StageGateOutput.advance`), not by mutating `session.stage` directly.

## Sync Engine Awareness

- `StageReviewCommitNode` provides final review output only; it does not introduce an extra submit-confirm gate.
- Undo remains available through Slack action handlers (`ff_timebox_undo_submit`) via orchestrator message handlers.
- `StageRefineNode` calls `TimeboxPatcher` then `apply_tb_ops()`, and then syncs the updated `TBPlan` through `CalendarSubmitter`.
- `StageSkeletonNode` is presentation-first: it produces the markdown overview and carries forward any pre-generated draft plan; it does not build the remote baseline snapshot.
- `StageRefineNode` is responsible for preparing missing `TBPlan`/baseline state before patch+sync.
- Presenter-attached stage controls must remain deterministic: always include `Proceed` (except final review/submit stage), plus `Back`/`Redo`/`Cancel`; readiness checks happen when `Proceed` is clicked.
- Nodes must never call `sync_engine.py` functions directly; always go through `CalendarSubmitter`.

## Stage Boundary Rules (Hard)

- `StageSkeletonNode` (Stage 3):
  - Must publish markdown overview via presenter markdown blocks.
  - Must not require or emit a validated `Timebox` as Stage 3 output.
  - Can carry `TBPlan` draft state forward for Stage 4 preparation.
- `StageRefineNode` (Stage 4):
  - Must patch from `TBPlan` through `TimeboxPatcher` retry loop.
  - Must pass validator failures back through retry context (no single-shot conversion fallback).
  - Must materialize `Timebox` only from successful patch-loop validation output.

## Adding a New Node

1. Create the class extending `_StageNodeBase` (for stage nodes) or `BaseChatAgent` (for infra nodes).
2. Register in `flow_graph.py` with `DiGraphBuilder`.
3. Add edge conditions for entry/exit.
4. Add unit tests in `tests/unit/` following existing `test_timeboxing_graphflow_state_machine.py` patterns.
5. Update this folder's `README.md` with the new node.
