# Timeboxing Nodes

GraphFlow node agents that implement the timeboxing stage machine. Each node is a `BaseChatAgent` consumed by `flow_graph.py`.

## File Index

| File | Contents |
|------|----------|
| `nodes.py` | All node classes (see below) |
| `__init__.py` | Re-exports node classes |

## Node Classes

### Infrastructure Nodes

| Node | Stage | Responsibility |
|------|-------|---------------|
| `TurnInitNode` | all | Receives the latest user message, stashes it on Session, produces the routing input for DecisionNode. |
| `DecisionNode` | all | Reads current `session.stage` and decides whether to advance, stay, or branch. Emits the stage label consumed by `TransitionNode`. |
| `TransitionNode` | all | Pure router: maps the decision label to the correct stage node. No LLM call. |
| `PresenterNode` | all | Formats the stage agent output into Slack Block Kit. One user-facing message per Slack turn. |

### Stage Nodes (all extend `_StageNodeBase`)

| Node | Stage | Responsibility |
|------|-------|---------------|
| `StageCollectConstraintsNode` | 1 | Builds constraint context (immovables + Notion + session constraints), calls the Stage 1 LLM via `stage_gating.py`, updates `session.frame_facts`. |
| `StageCaptureInputsNode` | 2 | Builds input context (frame_facts + user tasks/priorities), calls the Stage 2 LLM, updates `session.input_facts`. |
| `StageSkeletonNode` | 3 | Drafts the initial schedule. Produces a `Timebox`, converts to `TBPlan`, saves `base_snapshot`. |
| `StageRefineNode` | 4 | Sends `TBPlan` + user feedback to `TimeboxPatcher`. Applies returned `TBPatch` via `apply_tb_ops()`. Loops until user is satisfied. |
| `StageReviewCommitNode` | 5 | Presents final plan summary. On confirm: calls `CalendarSubmitter.submit_plan()` to sync to GCal. Logs `SyncTransaction`. |

### Base Class

`_StageNodeBase` provides common lifecycle:
1. Build typed context from Session fields.
2. Call the stage-specific LLM (via `stage_gating.py` prompt templates).
3. Parse `StageGateOutput` and update Session.
4. Cache `last_gate_output` for downstream nodes.

## Data Flow

```
User message
  -> TurnInitNode (stash on Session)
    -> DecisionNode (read stage, decide)
      -> TransitionNode (route)
        -> Stage*Node (LLM call, Session update)
          -> PresenterNode (Block Kit render -> Slack)
```

## Related

- Parent: `../README.md` (timeboxing module index)
- `../flow_graph.py`: builds the GraphFlow DAG that wires these nodes together
- `../stage_gating.py`: stage enum + LLM prompt templates consumed by stage nodes
- `../contracts.py`: typed stage context models consumed by stage nodes
