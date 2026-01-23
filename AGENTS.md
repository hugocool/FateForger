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
- Policy: prefer Pydantic DTOs + `ultimate-notion` (UNO) schema/page objects at boundaries (Slack/UI/agents); avoid leaking SQLModel persistence models outside storage layers.

## Timeboxing constraint extraction
- Durable extraction: `src/fateforger/agents/timeboxing/notion_constraint_extractor.py`
- Local/session extraction + Slack review still uses the SQLite `ConstraintStore` in `src/fateforger/agents/timeboxing/preferences.py`.
- Timeboxing LLMs can call the tool `extract_and_upsert_constraint`, which wraps the extractor agent and upserts into Notion.
- Slack wiring: Slack events route to `timeboxing_agent` via `StartTimeboxing` / `TimeboxingUserReply` messages (see `src/fateforger/slack_bot/handlers.py`).
- Durable constraints must be prefetched via the constraint-memory MCP server before Stage 1; this work is non-blocking.
- Stage-gating LLMs do not call tools; tool IO happens only in background coordinator tasks.
- Prefer AutoGen framework features for orchestration and control-flow (GraphFlow/`DiGraphBuilder`, termination conditions, message filtering, typed outputs via `output_content_type`, `FunctionTool`) instead of reinventing bespoke state machines.
- **Intent classification / natural-language interpretation must use LLMs (AutoGen agents) or explicit slash commands; never use regex/keyword matching.**
- **Never add deterministic “NLU” helpers** (e.g., parsing scope/date/intent from free-form user text). Use structured LLM outputs instead (see `src/fateforger/agents/timeboxing/nlu.py`). Delete any accidental deterministic NLU code on sight.

## Planning reminders
- Missing-planning nudges are scheduled by `PlanningReconciler` and must ignore stale anchor events outside the horizon window.
- Suppress planning nudges while a timeboxing session is active; idle sessions flip to `unfinished` after 10 minutes and re-trigger reconciliation.

## Environment variables
- `NOTION_TOKEN`: required by `ultimate-notion` for Notion API calls.
- `NOTION_TIMEBOXING_PARENT_PAGE_ID`: parent page where DBs are installed/reused.

## Local run (dev)
- Canonical stack is `docker-compose.yml` at repo root (the `infra/docker-compose-2.yml` file is legacy).
- VS Code tasks in `.vscode/tasks.json` start the stack (`FateForger: Compose Up (Core)` / `FateForger: Compose Up (Everything)`).

## Testing and test suite upkeep
- Keep a TODO checklist for test additions/updates while you work; clear it as tests are implemented.
- When you change behavior or integrations, add/expand tests to cover the new paths and edge cases.
- Keep iterating until the relevant tests pass; if a test is blocked, note the blocker and add a follow-up TODO.
- Tests must reflect the integration being worked on (MCPs, Slack handoffs, scheduling flows, etc.).
- Refactor tests to stay readable and DRY as the suite grows; avoid copy/paste drift.

## Python interpreter configuration (CRITICAL)
- **This project uses Python 3.11.9** via Poetry's virtual environment at `.venv/`.
- VS Code MUST be configured to use `.venv/bin/python` (not system Python or pyenv).
- **Common issue**: If you see `AttributeError: module 'asyncio.base_futures' has no attribute '_future_repr'`, the debugger is using the wrong Python interpreter.
- **Fix**: Command Palette → "Python: Select Interpreter" → choose `.venv/bin/python` (Python 3.11.9).
- Verify with: `~/.local/pipx/venvs/poetry/bin/poetry env info` should show `.venv` path and Python 3.11.9.

## Poetry runtime (required)
- Use the pipx-installed Poetry (v2.x): `~/.local/pipx/venvs/poetry/bin/poetry`.
- If you switch Python with pyenv: `~/.local/pipx/venvs/poetry/bin/poetry env use $(pyenv which python)` then re-check `poetry env info`.

## Docs, README, and AGENTS.md rules
- Every folder should contain a `README.md` that acts as an index; create one when adding a new folder or major feature area.
- Document user-facing features and setup in the relevant `README.md` or docs, but keep agent instructions in `AGENTS.md`.
- Update or add an `AGENTS.md` in a folder when: the folder has non-trivial workflows, tool usage, or constraints the agent should follow.
- If an `AGENTS.md` needs extra context, it can instruct the agent to read the local `README.md`.
- Keep docs current with the code; update the relevant `README.md` alongside implementation changes.

## Docs build/serve (MkDocs)
- Build docs: `make docs-build` (or `.venv/bin/mkdocs build --strict`)
- Serve docs: `make docs-serve` (or `.venv/bin/mkdocs serve`)
- Timeboxing refactor notes live in `TIMEBOXING_REFACTOR_REPORT.md`.

## Code hygiene
- Every function and method must include type annotations (including return types).
- Every function and method must include a docstring; update them when behavior changes.
- Prefer Pydantic validation at boundaries (Slack payloads, MCP tool results, JSON blobs) instead of try/except parsing and dict probing.
- Legacy/back-compat code paths must be tagged with `# TODO(refactor):` and removed once migrations land.
