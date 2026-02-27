---
title: Agents
---

## TimeboxingFlowAgent

Primary day-planning agent that runs the GraphFlow timeboxing workflow and coordinates:
- Stage-gated planning for day schedule drafts (typed JSON contexts per stage)
- Patch-based refinement (`TimeboxPatcher`)
- Constraint extraction + persistence (background, non-blocking)

Code: `src/fateforger/agents/timeboxing/agent.py`

Related docs:

- `docs/indices/agents_timeboxing.md`
- `docs/architecture/timeboxing_refactor.md`

## Constraint Memory + Extraction (Mem0-backed)

Constraint extraction turns user preference corrections into a deterministic
`constraint_record` and upserts it into Mem0 for durable future reuse.

- Output schema: typed `ConstraintBase`/`constraint_record` payloads
- Persistence: `Mem0ConstraintMemoryClient.upsert_constraint(...)`
- Orchestration path: `_queue_constraint_extraction` + `_queue_durable_constraint_upsert`
- Memory edit/review is explicit via `memory_*` tools in Stage 4/5.

Code:
- `src/fateforger/agents/timeboxing/mem0_constraint_memory.py`
- `src/fateforger/agents/timeboxing/durable_constraint_store.py`
- `src/fateforger/agents/timeboxing/agent.py`

## ConstraintRetriever

Gap-driven retriever for durable constraints that:
- derives a small query plan from stage + day context (gaps/blocks/immovables)
- uses `query_types` to select relevant `type_id`s (except Stage 1 startup path)
- then queries durable constraints with deterministic filters and typed post-filtering

Code:
- `src/fateforger/agents/timeboxing/constraint_retriever.py`
- `src/fateforger/agents/timeboxing/mem0_constraint_memory.py`
- `src/fateforger/agents/timeboxing/agent.py`

See full retrieval/update flow:
- `docs/architecture/constraint-flow/index.md`

## SlackBot Router + Review

Slack-facing routing + constraint review UI:
- Extracted constraints can be reviewed and accepted/declined via a Slack modal.
- Current implementation updates the local SQLite-backed constraint statuses.

Code:
- `src/fateforger/slack_bot/handlers.py`
- `src/fateforger/slack_bot/constraint_review.py`
