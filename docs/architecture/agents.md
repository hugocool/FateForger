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

## ConstraintExtractorAgent (Notion-backed)

Extractor agent that turns user preference corrections into a deterministic constraint record and
upserts it into Notion for durable future reuse.

- Output schema: `ConstraintExtractionOutput` (JSON, structured)
- Persistence: `NotionConstraintStore.upsert_constraint(...)` + `TB Constraint Events` audit log
- Timeboxing agents can call the tool `extract_and_upsert_constraint` (Agent-as-Tool under the hood).
- Notion access is via the constraint-memory MCP server (`scripts/constraint_mcp_server.py`).

Code:
- `src/fateforger/agents/timeboxing/notion_constraint_extractor.py`
- `src/fateforger/adapters/notion/timeboxing_preferences.py`

## ConstraintRetriever

Gap-driven retriever for durable constraints that:
- derives a small query plan from stage + day context (gaps/blocks/immovables)
- uses `constraint_query_types` to select relevant `type_id`s
- then queries constraints via `constraint_query_constraints` with those `type_id`s

Code:
- `src/fateforger/agents/timeboxing/constraint_retriever.py`
- `src/fateforger/agents/timeboxing/mcp_clients.py`
- `src/fateforger/agents/timeboxing/agent.py`

## (Next) ConstraintRetriever Improvements

Planned improvements:
- loads global/profile constraints first (high precedence)
- then queries only what is needed for remaining planning gaps ("degrees of freedom")
- uses structured Notion properties (no embeddings requirement)

Status: partially implemented; tracked in `lattice_ticket.md`.

## SlackBot Router + Review

Slack-facing routing + constraint review UI:
- Extracted constraints can be reviewed and accepted/declined via a Slack modal.
- Current implementation updates the local SQLite-backed constraint statuses.

Code:
- `src/fateforger/slack_bot/handlers.py`
- `src/fateforger/slack_bot/constraint_review.py`
