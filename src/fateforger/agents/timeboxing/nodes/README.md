# Timeboxing GraphFlow Nodes

This folder contains the node agents used by the GraphFlow-based timeboxing state machine.

Design rules:

- Nodes are **deterministic orchestration** components: they mutate `Session` and/or call the existing stage helpers.
- Nodes must not do intent classification via regex/keyword matching. When interpretation is needed, call the structured LLM helpers in `src/fateforger/agents/timeboxing/nlu.py`.
- Nodes do not call external tools directly; tool I/O remains owned by the coordinator (background tasks), consistent with `src/fateforger/agents/timeboxing/AGENTS.md`.
- Each Slack turn should run the graph until the `PresenterNode` emits a single user-facing message, then terminate.
