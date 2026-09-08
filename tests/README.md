# Tests

pytest suite for FateForger, organized by scope: unit, integration, e2e, plus
a few narrower suites (evals, replay, coordination, memory) described below.

## How to run

The suite runs against the shared venv directly, not through `poetry run`.
`poetry run pytest` happens to resolve to the same interpreter today, but a
`poetry install` inside a worktree silently repoints the parent `.venv`
(`AGENTS.md`, "Worktrees, e2e testing, and PRs" — the 2026-09-03 incident:
two bots answering one workspace on code 451 lines apart, traced to a
repointed `.venv`), so it is not reliable across worktrees. Use the venv's
python explicitly.

There is one `.venv`, and it lives in the main checkout. A worktree has none
of its own, so from a worktree the path is the main checkout's -- and do not
create a second one there, which is the mistake the incident above describes:

```bash
# The fast suite -- run this before every commit
.venv/bin/python -m pytest tests -m "not slow" -q

# Everything, including slow tests
.venv/bin/python -m pytest tests

# One subpackage
.venv/bin/python -m pytest tests/unit/timeboxing/ -v

# One file
.venv/bin/python -m pytest tests/unit/core/test_toon_encode.py -v

# By keyword
.venv/bin/python -m pytest tests -k timeboxing -v

# From a worktree, name the main checkout's interpreter
/path/to/admonish-1/.venv/bin/python -m pytest tests -m "not slow" -q
```

Nothing in `.github/workflows/` runs pytest today, so "the tests pass" means
someone ran them.

Markers (`pyproject.toml`): `slow`, `integration`, `unit`. `-m "not slow"` is
the suite you run before every commit; the slow tests hit real endpoints
(OpenRouter evals) or exercise timing-sensitive code and are excluded from the
fast loop on purpose, not by accident — run them deliberately when a change
touches what they cover.

`tests/integration/` needs running services (DB, MCP containers) and
`tests/e2e/` needs a Slack mock plus running services; both are excluded
unless you have those up. `tests/evals/` and `tests/memory/test_eval_*.py`
hit a real model over OpenRouter and are `slow` for that reason, not because
they're large.

## Structure

```
tests/
  conftest.py       # shared fixtures: sqlite engine, session, admonisher Base
  repo.py           # ROOT — the repo root, found rather than counted
  doubles/          # test doubles shared by more than one module
  unit/             # fast, isolated, no external services — subject subpackages
  integration/      # requires DB / MCP containers
  e2e/              # full Slack flow simulation
  evals/            # quality evals against a real model (slow)
  replay/           # incident replays against recorded inputs
  coordination/     # multi-session claim-protocol plumbing
  memory/           # the standalone memory server's own suite (see below)
  fixtures/         # frozen inputs the evals and replays read
```

### `tests/unit/` subpackages

Each subpackage is a subject, not a file-naming convention. Before adding a
new unit test, find the subpackage whose subject it extends; open a new one
only when none fits.

| Subpackage | Subject |
|---|---|
| `timeboxing/` | The stage-gated timeboxing agent: the adaptive kernel, stage cards and prompts, the sync engine (`TBPlan`/`TBOp`/patching), calendar reconciliation |
| `slack/` | The Slack surface: card rendering, message routing, the `/dsh` harness, planning/task/timebox Slack flows |
| `constraints/` | The constraint memory pipeline as FateForger calls it: extraction, the Notion/KG stores, NLU frame slots, MCP tool wiring — as opposed to `tests/memory/`, which is the memory server's own suite |
| `core/` | Cross-cutting infrastructure not owned by one agent: settings, logging/observability, MCP schema and URL validation, contracts, environment sanity, alembic |
| `haunt/` | `src/fateforger/haunt/`: reminder orchestration, planning-session store, calendar reconciliation, the required-block rule |
| `tasks/` | The task board and its Notion/TickTick tool wiring |
| `schedular/` | Admonisher models, the planner agent, revisor handoff, routing prompts |
| `tmbx/` | The `tmbx` package: ops, patching, journaling, calendar ports, the render layer — pre-existing, kept as the model the 2026-09 reorganization followed |

`tests/memory/` is not under `tests/unit/`: it is `src/memory/`'s own suite
— a standalone, agent-agnostic MCP server that imports nothing from
`fateforger.*` (see `CLAUDE.md`, "The memory server"). Running it through
`pytest` needs no extra setup — `pyproject.toml` sets `pythonpath = ["src"]`
for the whole suite — but any script or tool call against `src/memory/`
outside pytest needs `PYTHONPATH=src` set explicitly, per that package's own
rule.

### `tests/doubles/`

A double used by two or more test modules lives here, not in whichever test
file happened to define it first. Two things went wrong under the old
pattern, both now fixed:

- Nine modules had each grown their own `DummyClient` for the Slack web
  client, differing only in the timestamps they invented — nine copies of one
  fake, silently drifting.
- Several modules imported a double out of another *test* file. Under
  pytest's `--import-mode=importlib` (set in `pyproject.toml`), a test
  module's import of another test module only works if the exporter has
  already been collected — an accident of collection order, not something
  the test asserts or that survives a reorganization.

Current doubles: `slack.py` (`RecordingSlackClient`, the one Slack web-client
fake), `haunt.py` (calendar + scheduler doubles for the planning reconciler),
`planning.py` and `planning_card.py` (the planning-reminder dispatch and
add-to-calendar doubles), `required_block.py` (calendar/constraint-store/
ledger doubles for the required-block watcher), `timeboxing.py` (ports for
the adaptive-timeboxing kernel, shared with `tests/replay/`).

If you're about to write a fake that plausibly serves more than one test
file, put it here instead of in the first file that needs it.

### `tests/repo.py`

Exports `ROOT`: the repository root, found by walking up from the test file
to the first parent containing `pyproject.toml`. Tests used to spell this
`Path(__file__).resolve().parents[2]` — twenty of them did — which encodes
how deep the file happens to sit and breaks with a `FileNotFoundError` the
moment the file moves one directory down, as it did when the flat `unit/`
directory was reorganized into subpackages. Use `from tests.repo import
ROOT` instead of counting `parents[N]`.

## What's out of `tests/unit/`

Six test modules were deleted in the 2026-09 reorganization because their
subject has no caller anywhere in `src/` or `scripts/`: `test_backoff.py`
(`admonisher.base`), `test_calendar_haunter.py` and
`test_calendar_haunter_integration.py` (`admonisher.calendar`),
`test_diffing_agent.py` (`schedular.diffing_agent`), `test_timeboxing_flow.py`
(`timeboxing.flow`), and `test_timeboxing_notebook_entrypoints.py`
(`timeboxing.notebook_entrypoints`) — the last of these asserted the line
numbers of methods in a code-navigation helper. Deleting the dead *source*
modules themselves is a separate, out-of-scope change with its own PR (the
evidence for each is in that PR's body); this one only removed the tests
that had nothing left to guard.

`CALENDAR_QUERY_LOCATIONS.md` and `MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md`
at the repo root document the pre-AutoGen `CalendarHaunter` (`admonisher.calendar`)
as production; nothing has constructed it since haunting moved to
`fateforger/haunt/`, so both are now marked superseded rather than rewritten
— they stay as a record of what was true when written, with a pointer to
what replaced it.
