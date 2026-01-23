# Timeboxing GraphFlow Nodes — Agent Notes

This folder contains the **GraphFlow** node agents that implement the timeboxing stage machine.

## Design Rules (Critical)

- **No regex/keyword intent parsing.** Intent classification and natural-language interpretation must use LLMs (AutoGen agents) or explicit Slack slash commands.
- **Nodes are orchestration-only.** Graph nodes should be deterministic and lightweight; they should not perform business logic that belongs in `TimeboxingFlowAgent`.
- **No tool IO from stage-gating LLMs.** Stage gates are LLM-only and must not call tools. Tool IO (calendar, constraint-memory, durable upserts) remains in the coordinator via background tasks.
- **One user-facing message per turn.** The graph must run until `PresenterNode` emits a single `TextMessage`, then terminate.
- Prefer AutoGen GraphFlow primitives (edges + conditions + activation groups/conditions + termination) instead of bespoke routing/state machines inside node logic.

## GraphFlow Safety (Activation Semantics)

`DiGraphBuilder.add_edge(...)` defaults to:

- `activation_group = <target node name>`
- `activation_condition = "all"`

If a node has **multiple parents** (e.g., `PresenterNode`), you must set `activation_condition="any"` on its incoming edges, otherwise GraphFlow may terminate early without scheduling the node.

Reference:
- Graph builder: `src/fateforger/agents/timeboxing/flow_graph.py`

## Message Types

- Use `StructuredMessage` only for small routing/state signals (`FlowSignal`, `StageDecision`).
- Avoid emitting large, deeply-nested objects in `StructuredMessage` unless necessary; it can cause noisy serialization warnings in GraphFlow logs.
- Only `PresenterNode` should emit user-facing `TextMessage` content.

## Editing Checklist

When adding/changing nodes or edges:

- Ensure edges into multi-parent nodes use `activation_condition="any"`.
- Ensure the graph still terminates via `TextMessageTermination(source="PresenterNode")`.
- Add/adjust unit tests in `tests/unit/test_timeboxing_graphflow_state_machine.py`.
