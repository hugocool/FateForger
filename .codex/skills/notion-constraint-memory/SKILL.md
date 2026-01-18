---
name: notion-constraint-memory
description: Use the constraint-memory MCP tools to query/upsert timeboxing constraints stored in Notion.
compatibility: network
metadata:
  owner: fateforger
  version: "0.1.0"
---

Use this skill when working with the timeboxing preference memory stored in Notion.
Do NOT call Notion APIs directly; use the MCP tools from the constraint-memory server.

Tools
- `constraint.get_store_info()`
- `constraint.get_constraint(uid)`
- `constraint.query_types(stage, event_types)`
- `constraint.query_constraints(filters, type_ids, tags, sort, limit)`
- `constraint.upsert_constraint(record, event)`
- `constraint.log_event(event)`
- `constraint.seed_types()`

Server
- Script: `scripts/constraint_mcp_server.py`
- Transport: stdio (recommended)

Environment
- `NOTION_TOKEN`: Notion integration token.
- `NOTION_TIMEBOXING_PARENT_PAGE_ID`: parent page where the DBs live.

Usage notes
- Always call `constraint.query_types` before expanding to constraint queries.
- Use `constraint.upsert_constraint` with an event payload for audit logging when possible.
