# Repo Agent Notes (admonish-1)

## Notion preference memory
- Notion is the intended source of truth for durable timeboxing preferences.
- The Notion schemas + store wrapper live in `src/fateforger/adapters/notion/timeboxing_preferences.py`.
- The constraint-memory MCP server wraps all Notion access: `scripts/constraint_mcp_server.py`.
- A repo skill exists at `.codex/skills/notion-constraint-memory/SKILL.md` with tool usage rules.

## Timeboxing constraint extraction
- Durable extraction: `src/fateforger/agents/timeboxing/notion_constraint_extractor.py`
- Local/session extraction + Slack review still uses the SQLite `ConstraintStore` in `src/fateforger/agents/timeboxing/preferences.py`.
- Timeboxing LLMs can call the tool `extract_and_upsert_constraint`, which wraps the extractor agent and upserts into Notion.
- Slack wiring: Slack events route to `timeboxing_agent` via `StartTimeboxing` / `TimeboxingUserReply` messages (see `src/fateforger/slack_bot/handlers.py`).

## Environment variables
- `NOTION_TOKEN`: required by `ultimate-notion` for Notion API calls.
- `NOTION_TIMEBOXING_PARENT_PAGE_ID`: parent page where DBs are installed/reused.
