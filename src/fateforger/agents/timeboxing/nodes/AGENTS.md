# Timeboxing Nodes — Agent Notes

**Scope:** Operational rules for `nodes/`. For file index and data flow, see `README.md` in this folder.

## Design Rules

- Each node is a `BaseChatAgent` with `on_messages()` / `on_messages_stream()`.
- Nodes are **stateless between turns**; all mutable state lives on the shared `Session` object passed at construction.
- Nodes must not import or call MCP clients, Slack SDK, or database layers. Tool IO is the coordinator's job.
- Nodes should not catch and swallow exceptions; let them propagate so the coordinator can handle them.
- One user-facing message per Slack turn; `PresenterNode` is the only node that produces user-visible output.

## GraphFlow Safety

- Every node must be registered in `flow_graph.py` via `DiGraphBuilder`; do not instantiate nodes outside the graph.
- Edge conditions in `flow_graph.py` are the single source of truth for stage transitions; do not hard-code transitions in nodes.
- If a node needs to signal "stay in current stage" vs "advance", it must do so via its return value (e.g., `StageGateOutput.advance`), not by mutating `session.stage` directly.

## Sync Engine Awareness

- `StageReviewCommitNode` must not auto-submit; it sets `session.pending_submit=True` and returns review output for Presenter.
- Actual submission/undo are triggered by Slack action handlers (`ff_timebox_confirm_submit`, `ff_timebox_undo_submit`) via orchestrator message handlers.
- `StageRefineNode` calls `TimeboxPatcher` then `apply_tb_ops()` — it updates `session.tb_plan` but does NOT sync to calendar (sync only happens at Stage 5).
- `StageSkeletonNode` produces the initial `TBPlan` and saves `session.base_snapshot` — this is the reference for later diff-based sync.
- Nodes must never call `sync_engine.py` functions directly; always go through `CalendarSubmitter`.

## Adding a New Node

1. Create the class extending `_StageNodeBase` (for stage nodes) or `BaseChatAgent` (for infra nodes).
2. Register in `flow_graph.py` with `DiGraphBuilder`.
3. Add edge conditions for entry/exit.
4. Add unit tests in `tests/unit/` following existing `test_timeboxing_graphflow_state_machine.py` patterns.
5. Update this folder's `README.md` with the new node.
