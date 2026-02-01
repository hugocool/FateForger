# Repo Agent Notes (admonish-1)

**Scope:** Project-wide conventions and cross-cutting invariants. For module-specific workflows/constraints, check for nested `AGENTS.md` files first.

## Operating rules
- `AGENTS.md` files are for agent operating rules/invariants only. Put architecture, APIs, schemas, and feature documentation in the relevant `README.md` files or `docs/`.
- Before editing a folder/module, check that folder’s `AGENTS.md`; add one if the folder has non-trivial workflows or constraints.
- For multi-step work, write a short plan first and keep it updated as scope changes.
- **Ticket + acceptance criteria first (required):** before implementing any new functionality or behavior change, co-create a small “ticket” with the user that includes acceptance criteria and ownership boundaries (see below). Do not start coding until the ticket is agreed.
- Keep edits minimal and consistent with existing conventions; prefer shared helpers/utilities over duplicated logic.
- For new features/bug fixes or integration changes, add/adjust tests and run the relevant subset of the suite before finishing.
- Avoid editing generated/artifact outputs and local state (e.g. `site/`, `*.db`, `logs/`, token/secret folders) unless explicitly requested.
- Ask before adding dependencies or changing schemas/DB models; deliver schema changes via Alembic migrations (no runtime “ensure_*” table/column creation in live paths).

## Ticketing & acceptance criteria (critical)
Before any implementation work:
- **Inventory the current state:** identify what already exists, where responsibilities live, and what should be reused vs created (avoid duplicating behavior; stay DRY).
- **Compose the change:** agree on where new code should live (module/folder ownership), how it integrates with existing flows (Slack/MCP/agents/scheduler), and any boundaries/contracts.
- **Draft the ticket with the user:** capture the minimum required fields:
  - Goal (1–2 sentences)
  - Scope / non-goals
  - Acceptance criteria (observable; ideally Given/When/Then; include failure cases)
  - Design/ownership notes (which module owns what; key entry points)
  - Validation plan (tests to add/run + any required end-to-end verification steps)

After implementation:
- **Walk through acceptance criteria with the user:** explicitly check each criterion and record whether it is satisfied.
- **DoD is only met when:** (a) acceptance criteria are satisfied, (b) relevant automated tests pass, and (c) docs/indices are updated to reflect the new behavior and status.
- **Docs must reflect reality:** update the nearest folder `README.md` (and any relevant `docs/` pages) to reflect the current status (Roadmap/WIP/Implemented/Documented/Tested/User-confirmed working).

## Tech stack & repo conventions
- Runtime: Python 3.11.9 via Poetry’s local virtualenv at `.venv/` (VS Code should use `.venv/bin/python`).
- App stack: FastAPI + Uvicorn, Slack Bolt/SDK (Socket Mode), AutoGen (`autogen-core` / `autogen-agentchat`) with MCP (`autogen-ext[mcp]`), OpenAI SDK, Notion via `ultimate-notion`.
- Storage/migrations: SQLAlchemy (async) + SQLModel + SQLite + Alembic.
- UI tooling: setup/diagnostics wizard is a small FastAPI UI; some utilities use Gradio.
- Formatting/tests: Black line length 88, pytest (+ `pytest-asyncio`).
- `src/` layout + notebooks: `src/` is the intended import root. In VS Code, `python.analysis.extraPaths` includes `./src` and notebooks are intended to run with `jupyter.notebookFileRoot=${workspaceFolder}/src`; do not add `sys.path`/bootstrap “import hacks” cells in notebooks—fix the working directory/kernel selection instead.

## Project map (high level)
- `src/fateforger/`: application code (bots, agents, adapters, Slack wiring, setup wizard).
- `scripts/`: local tools (including MCP servers/wrappers).
- `tests/`: pytest suite.
- `docs/` + `mkdocs.yml`: MkDocs documentation.
- `notebooks/`: exploratory/dev notebooks (should import from `src/` without bootstrap code).

## Subfolder `AGENTS.md` + docs/status workflow (critical)
- **Separation of concerns:** put “how to operate as an agent” rules in `AGENTS.md`; put “what the system does” (architecture, APIs, behavior, acceptance criteria, runbooks) in `README.md` files or `docs/`.
- **Nested `AGENTS.md` creation/update:** when you touch a folder with non-trivial workflows/constraints, add or update that folder’s `AGENTS.md` (scoped to that subtree). Keep it short and specific.
- **Progressive indexing:** every non-trivial folder should have a `README.md` that acts as an index:
  - what this folder is for, key entry points/classes, how to run the relevant tests, and links to deeper docs.
  - if multiple implementations exist (prod vs archive vs example), enumerate them explicitly (see `DOCS_INDEX.md` style).
- **Status must be explicit:** when adding/changing behavior, update the nearest `README.md`/doc to include a `Status` section that answers “what is true today?” (not aspirational).
- **Status lives with the code:** track status in the nearest folder `README.md` (as a `Status` section) and in code via lightweight markers (`# WIP:` / `# TODO:` / `# TODO(refactor):`). Update/remove these as work progresses.

### Status taxonomy (use consistently)
- **Roadmap:** desired behavior planned but not implemented.
- **WIP:** partially implemented; expected to be flaky or incomplete; not ready for users.
- **Implemented:** code path exists, but may be undocumented/untested.
- **Documented:** docs updated with current behavior + usage + limitations.
- **Tested:** covered by automated tests that pass locally/CI for the relevant scope.
- **User-confirmed working:** a human has run the real workflow (or a realistic end-to-end dev stack) and confirmed acceptance criteria on a specific date.

### Definition of done (DoD) rule
- Do not claim “working” unless **Tested** and/or explicitly **User-confirmed working** is recorded; otherwise, label as **Implemented**/**WIP** and list the exact validation steps still needed.
- For integrations (Slack/MCP/Notion/Calendar), “Tested” can be unit/contract tests, but “User-confirmed working” requires an end-to-end run against the actual integration environment.
- **Per-interaction status reporting:** when you finish a change, explicitly state (1) current status (Roadmap/WIP/Implemented/Documented/Tested/User-confirmed), (2) which tests/commands you ran, and (3) what still needs human verification (with concrete steps + where to record the confirmation date in docs).

### In-code status markers
- Use `# WIP:` for behavior that is intentionally incomplete and should not be treated as done.
- Use `# TODO:` for concrete follow-ups tied to acceptance criteria or missing validation.
- Use `# TODO(refactor):` for legacy/back-compat/cleanup work that should be removed once migrations land.
- When a `# WIP:`/`# TODO:` is resolved, delete it (do not let stale markers accumulate).

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
- Serve docs: `make docs-serve` (override port with `MKDOCS_DEV_ADDR=127.0.0.1:8001 make docs-serve`)
- Timeboxing refactor notes live in `TIMEBOXING_REFACTOR_REPORT.md`.

## Code hygiene
- Every function and method must include type annotations (including return types).
- Every function and method must include a docstring; update them when behavior changes.
- Prefer Pydantic validation at boundaries (Slack payloads, MCP tool results, JSON blobs) instead of try/except parsing and dict probing.
- Legacy/back-compat code paths must be tagged with `# TODO(refactor):` and removed once migrations land.
