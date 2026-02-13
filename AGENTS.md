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

## Issue/PR tracking & acceptance criteria (critical)
Before any implementation work:
- **GitHub is the system of record:** use a GitHub Issue for task state and a GitHub PR for implementation/validation state.
- **`/tickets/` is optional and temporary:** markdown ticket files may be used as local drafting aids, but they must mirror the GitHub Issue and are not authoritative.
- **Inventory the current state:** identify what already exists, where responsibilities live, and what should be reused vs created (avoid duplicating behavior; stay DRY).
- **Issue branching:** use issue-linked branches (e.g. `issue/<id>-<slug>`); keep issue, branch, notebook, and PR linked.
- **Compose the change:** agree on where new code should live (module/folder ownership), how it integrates with existing flows (Slack/MCP/agents/scheduler), and any boundaries/contracts.
- **Draft the issue payload with the user:** capture the minimum required fields:
  - Goal (1–2 sentences)
  - Scope / non-goals
  - Acceptance criteria (observable; ideally Given/When/Then; include failure cases)
  - Design/ownership notes (which module owns what; key entry points)
  - Validation plan (tests to add/run + any required end-to-end verification steps)

After implementation:
- **Walk through acceptance criteria with the user:** explicitly check each criterion and record whether it is satisfied.
- **DoD is only met when:** (a) acceptance criteria are satisfied, (b) relevant automated tests pass, and (c) docs/indices are updated to reflect the new behavior and status.
- **Docs must reflect reality:** update the nearest folder `README.md` (and any relevant `docs/` pages) to reflect the current status (Roadmap/WIP/Implemented/Documented/Tested/User-confirmed working).
- **Keep GitHub current:** reflect progress and outcomes in the Issue + PR; do not rely on repo-local markdown as long-term status tracking.
- **Repo cleanliness before merge:** remove temporary ticket markdown/scratch artifacts unless explicitly retained as durable docs.

## Repo cleanliness gates (critical)
- **Start-of-work cleanliness check:** run `git status --porcelain` and record whether the worktree is clean before making changes.
- **If the repo is not clean at start:** document the baseline dirty set (Issue comment or notebook metadata) and avoid mixing unrelated edits into the active PR scope.
- **Pre-PR-close cleanliness check:** run `git status --porcelain` again; only intended files for the issue should remain in scope.
- **Sprawl prevention:** before merge, remove temporary scratch files and notebook-local duplicates that were already extracted to durable artifacts.

## PR/Issue sync protocol (critical)
- **Primary operator surface:** progress must be visible in GitHub Issue/PR (including VS Code GitHub Pull Requests panel), not hidden in local ticket markdown.
- **Deterministic sync checkpoints:** when a PR exists, update GitHub at:
  - kickoff (scope + acceptance criteria + planned validation)
  - each substantial implementation checkpoint
  - pre-close (tests run, cleanliness check result, remaining human actions)
- **Update mechanism:** either post a PR comment or update the PR description; status must stay current without requiring users to open `/tickets/`.
- **Issue linkage rule:** if an Issue exists, keep it synchronized with the PR (or link to the latest PR checkpoint comment).
- **End-of-reply status footer (mandatory):** every agent reply during active implementation must end with a short `Issue/PR Sync` block containing:
  - issue URL/ID (or `none`)
  - PR URL/ID (or `none`)
  - branch name
  - workflow status (`Roadmap`/`WIP`/`Implemented`/`Documented`/`Tested`/`User-confirmed`)
  - next deterministic step
- **Fallback rule:** if GitHub write access is unavailable, state the blocker and provide copy-ready Issue/PR update text in the same reply.

## GitHub skills usage (stage-mapped workflow)
- Use the GitHub skills as workflow operators; keep all outputs visible in Issue/PR.
- Skill inventory for this workflow:
  - `gh-workflow-sync`: deterministic Issue/PR lifecycle sync (bootstrap + checkpoint updates).
  - `gh-address-comments`: address reviewer comments and review threads.
  - `gh-fix-ci`: inspect and resolve failing GitHub Actions checks.
- Stage mapping (required):
  - **Stage A (Kickoff / ticket activation):**
    - use `gh-workflow-sync` `bootstrap` to align Issue + issue branch + draft PR when starting a work item
    - use `gh-workflow-sync` `checkpoint --stage kickoff` after setup so the PR panel reflects current state
  - **Stage B (Implementation checkpoints):**
    - use `gh-workflow-sync` `checkpoint --stage progress` at each substantial change batch
    - optionally update bounded PR body sync block via `--update-pr-body`
  - **Stage C (Review comments):**
    - use `gh-address-comments` when review threads/comments are actionable
    - after fixes land, post a `gh-workflow-sync` progress checkpoint
  - **Stage D (CI failures):**
    - use `gh-fix-ci` when GitHub Actions checks fail
    - after remediation, post a `gh-workflow-sync` progress checkpoint
  - **Stage E (Pre-close / merge readiness):**
    - run required tests and cleanliness checks
    - use `gh-workflow-sync` `checkpoint --stage pre-close` with final status + remaining human actions
    - if temporary `/tickets/` markdown is slated for deletion, ask for explicit user confirmation first, then record action in PR/Issue
- These skills support the PR/Issue sync protocol; they do not replace acceptance-criteria verification or human sign-off.

## Workflow evolution protocol (critical)
- This workflow is intentionally adaptable. Changes are allowed through a controlled trial loop.
- **Canonical change channel:** open a GitHub issue for workflow changes (recommended prefix: `workflow/`).
- Each workflow-change issue must include:
  - current rule
  - proposed change
  - rationale and expected tradeoffs
  - trial scope (which issues/PRs will use it)
  - success/failure signals
  - rollback condition
- **Trial mode:** mark new rules as `trial` in the relevant `AGENTS.md` scope with owner + date.
- **Evaluation window:** run trial rules for 1-2 PRs (or a clearly defined timebox), then review outcomes in the workflow issue.
- **Decision outcomes:** promote to default, revise-and-retrial, or revert.
- **Promotion rule:** promote local/subfolder rules first; only move to root `AGENTS.md` after repeated success across contexts.
- **Preference-change confirmation gate (mandatory):**
  - if the agent detects a new or updated user workflow preference, it must propose the change first
  - it must wait for explicit user confirmation before editing any instruction/config files
  - no silent or inferred updates to workflow rules
- **Invariant protection:** do not relax core invariants while experimenting:
  - GitHub Issue/PR remain system of record
  - human sign-off remains mandatory
  - cleanliness gates remain mandatory
  - tests/docs requirements remain mandatory

## Workflow config source (critical)
- Keep mutable workflow parameters in `workflow_config/workflow_preferences.yaml`.
- Keep `AGENTS.md` focused on invariants, process contracts, and governance logic.
- Instruction/config files that require explicit user confirmation before edits:
  - `AGENTS.md`
  - `notebooks/AGENTS.md`
  - `notebooks/WIP/AGENTS.md`
  - `notebooks/DONE/AGENTS.md`
  - `workflow_config/workflow_preferences.yaml`

## Notebook-first development protocol (critical)
- Notebook-first development is supported: use notebooks as the external workbench for exploration, prototyping, prompt iteration, and live-output inspection.
- Notebooks are not the long-term source of truth for production behavior. Final ownership lives in:
  - `src/` for implementation
  - `tests/` for automated tests
  - `README.md`/`docs/` for durable documentation
  - GitHub Issue + PR for work status and validation traceability
- For each active issue, define one primary working notebook path (typically under `notebooks/WIP/`) and record it in the Issue/PR.
- Every issue notebook should begin with a short metadata section (first markdown cell) containing:
  - status (`WIP` | `Extraction complete` | `DONE` | `Reference` | `Archived`)
  - owner
  - issue URL/ID
  - issue branch
  - PR URL/ID (or `TBD`)
  - acceptance criteria being exercised in the notebook
  - last clean run date + environment marker (e.g., `.venv`, Python version)
  - repo cleanliness snapshot (`git status --porcelain`: clean/dirty + timestamp)
- Notebook lifecycle states (use explicitly):
  - `WIP notebook`: active development scratchpad, may contain temporary code.
  - `DONE notebook`: DoD criteria met; notebook reruns cleanly; duplicated production logic already extracted.
  - `Extraction complete`: transitional state before DONE; production code moved to modules/tests/docs; notebook still contains evidence and notes.
  - `Reference notebook`: kept intentionally for architecture examples, live integration recipes, or analysis.
  - `Archived notebook`: historical record; not used for active development.
- Before opening/closing a PR from notebook-driven work:
  - extract production code cells into modules
  - extract deterministic checks into pytest tests
  - move user-facing docs from markdown cells into `README.md`/`docs/`
  - update issue/PR with what was extracted and what intentionally remains notebook-only
- "Empty notebook after extraction" means remove duplicated production code cells, keeping only:
  - minimal repro cells
  - live/manual validation steps (if keys/real services are required)
  - architecture/analysis notes that are intentionally notebook-native
- Required notebook checkpoints:
  - before changing status to `Extraction complete`, rerun notebook from a clean kernel top-to-bottom
  - if rerun fails, keep status at `WIP` and record blockers in the issue/PR
- Vibe-coding role contract (human + coding agent):
  - human owns decisions: acceptance criteria, API boundaries, risk acceptance, and final merge sign-off
  - coding agent owns implementation mechanics: drafting/refactors, extraction to modules, test/doc scaffolding
  - handshakes are mandatory at ambiguity, extraction boundary, and final verification
- Ticket/branch/issue alignment is mandatory:
  - GitHub Issue is canonical for status; GitHub PR is canonical for implementation/validation evidence
  - notebook header must match current issue/branch/PR linkage
  - `/tickets/` markdown (if used) must mirror GitHub and stay temporary
  - PR description must summarize notebook-derived changes and remaining notebook artifacts
  - PR description must include `Notebook -> Artifact mapping` and `Verification performed`
  - when PR is merged and docs are updated, remove temporary `/tickets/` files unless explicitly retained as documentation
- Notion/GitHub synchronization:
  - keep issue/PR/ticket status aligned during the ticket
  - when requested, help draft GitHub issues/PR text and apply updates on user confirmation
- Collaboration rule: when working from a notebook, do extraction in small checkpoints so the user can review and steer before large code moves.
- Anti-patterns (forbidden):
  - do not leave critical logic only in notebooks once marked `Extraction complete`
  - do not let agents open/prepare notebook-heavy PRs without a linked ticket + deterministic run check
  - do not treat agent output as self-verifying; human review and tests remain required

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
- `workflow_config/`: mutable workflow preference parameters (separate from `AGENTS.md` contracts).

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
