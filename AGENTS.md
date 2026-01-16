# Repo Agent Notes (admonish-1)

## Setup & Diagnostics wizard
- A small FastAPI web UI is available for production deployments to guide setup and verify health of:
	- Slack (Socket Mode)
	- Google Calendar MCP
	- Notion MCP
	- TickTick MCP
- Source: `src/fateforger/setup_wizard/` (see its AGENTS.md).
- Docker Compose service: `setup-wizard` (binds `${WIZARD_HOST_PORT}:8080`).

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

## Local run (dev)
- Canonical stack is `docker-compose.yml` at repo root (the `infra/docker-compose-2.yml` file is legacy).
- VS Code tasks in `.vscode/tasks.json` start the stack (`FateForger: Compose Up (Core)` / `FateForger: Compose Up (Everything)`).
