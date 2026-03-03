---
title: Constraint Flow
---

# Constraint Flow

Source of truth diagram: `docs/architecture/constraint-flow.d2`.

This diagram reflects the current timeboxing constraint lifecycle:
- Stage 0 date confirmation triggers background durable prefetch.
- Session-local constraints are extracted/persisted during turns.
- `_collect_constraints()` merges durable + local constraints into `session.active_constraints`.
- Active constraints are injected into all stage prompts.
- Stage 4 supports repeated patch loops; Stage 5 corrections route back to Stage 4.
- Memory extraction/persist remains background/non-blocking.

## D2 Source

```text
See: docs/architecture/constraint-flow.d2
```

## Render (optional)

If `d2` is installed locally:

```bash
d2 docs/architecture/constraint-flow.d2 docs/architecture/constraint-flow.svg
```

Then embed the SVG where needed.
