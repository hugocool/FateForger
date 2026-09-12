# FateForger

*Being productive is no longer optional.* FateForger is a Slack-native planning agent: it reads
and writes your Google Calendar, remembers the constraints you state in conversation, and nudges
you when the day has drifted from the plan.

Agent rules live in [`AGENTS.md`](AGENTS.md) (Claude Code reads it through the `CLAUDE.md` stub).
This file is for humans and describes what the system *is* and how to run it.

## Tech stack

- **Runtime:** Python 3.11.9, in the local virtualenv at `.venv/`. VS Code must be pointed at
  `.venv/bin/python` — if the debugger raises
  `AttributeError: module 'asyncio.base_futures' has no attribute '_future_repr'`, it is on the
  wrong interpreter (Command Palette → *Python: Select Interpreter*).
- **App:** FastAPI + Uvicorn, Slack Bolt/SDK in Socket Mode, AutoGen (`autogen-core`,
  `autogen-agentchat`) with MCP via `autogen-ext[mcp]`, OpenAI SDK.
- **Storage:** SQLAlchemy (async) + SQLModel + SQLite, migrated with Alembic.
- **UI tooling:** the setup/diagnostics wizard is a small FastAPI UI; some utilities use Gradio.
- **Formatting and tests:** Black at line length 88, pytest with `pytest-asyncio`.
- **Package manager:** uv is the decided package manager; the migration off Poetry is
  [#453](https://github.com/hugocool/FateForger/issues/453) and has not landed, so the commands
  below are still the Poetry ones.

## Project map

| path | what lives there |
|---|---|
| `src/fateforger/` | application code — bots, agents, adapters, Slack wiring, setup wizard |
| `src/tmbx/` | the timebox models, the patch ops and the journal |
| `src/memory/` | the standalone constraint-memory MCP server (imports nothing from `fateforger`) |
| `src/trmnl_frontend/` | the TRMNL e-ink dashboard templates |
| `scripts/` | local tools, MCP servers and wrappers, `demo.py` |
| `tests/` | the pytest suite |
| `docs/` + `mkdocs.yml` | MkDocs documentation |
| `observability/` | the standalone local observability stack and its operator playbook |
| `workflow_config/` | mutable workflow parameters, kept out of the rulebook |

`src/` is the import root. Do not add `sys.path` bootstrap hacks — fix the working directory or
the kernel/interpreter selection instead.

## Getting started

```bash
poetry install
cp .env.template .env
poetry run python scripts/init_db.py
./run.sh
```

`.env` also carries the OpenRouter configuration and the two model pins; those pins are a decision
record, not a default — see `AGENTS.md`.

### Environment variables worth knowing

- `NOTION_TOKEN` — required by `ultimate-notion` for Notion API calls.
- `NOTION_TIMEBOXING_PARENT_PAGE_ID` — parent page where databases are installed or reused.
- `TIMEBOX_SESSION_DEBUG_LOG` / `TIMEBOX_PATCHER_DEBUG_LOG` — deterministic file logs during
  manual testing; see `observability/AGENTS.md`.

## Running the stack locally

The stack is `docker-compose.yml` at the repo root, and it is the only compose file for it. It
pins `name: admonish-1`, so volumes keep one set of names no matter where you run it from. VS Code
tasks in `.vscode/tasks.json` start it (*FateForger: Compose Up (Core)* / *(Everything)*).

For the three-process demo stack used for end-to-end runs:

```bash
./.venv/bin/python scripts/demo.py start
./.venv/bin/python scripts/demo.py status
```

## Setup & diagnostics wizard

A small FastAPI web UI for production deployments that guides setup and verifies the health of
Slack (Socket Mode), Google Calendar MCP, Notion MCP and TickTick MCP. Source:
`src/fateforger/setup_wizard/` (see its `README.md`). Docker Compose service: `setup-wizard`,
binding `${WIZARD_HOST_PORT}:8080`.

## Docs

```bash
make docs-build     # or: .venv/bin/mkdocs build --strict
make docs-serve     # MKDOCS_DEV_ADDR=127.0.0.1:8001 make docs-serve to change the port
```

See [docs](docs/index.md) for the rendered documentation.
