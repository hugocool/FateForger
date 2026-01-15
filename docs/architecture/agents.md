---
title: Agents
---

## TimeboxingFlowAgent

Primary day-planning agent that runs the GraphFlow timeboxing workflow and coordinates:
- LLM planning (`GraphFlow`) for day schedule drafts
- Patch-based refinement (`TimeboxPatcher`)
- Constraint extraction + persistence

Code: `src/fateforger/agents/timeboxing/agent.py`

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

## (Planned) ConstraintRetriever

Greedy, top-down retriever that:
- loads global/profile constraints first (high precedence)
- then queries only what is needed for remaining planning gaps (“degrees of freedom”)
- uses structured Notion properties (no embeddings requirement)

Status: not implemented; tracked in `lattice_ticket.md`.

## SlackBot Router + Review

Slack-facing routing + constraint review UI:
- Extracted constraints can be reviewed and accepted/declined via a Slack modal.
- Current implementation updates the local SQLite-backed constraint statuses.

Code:
- `src/fateforger/slack_bot/handlers.py`
- `src/fateforger/slack_bot/constraint_review.py`
