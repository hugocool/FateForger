# 📋 Ticket: Bring the docs back in line with the legacy-agent retirement

## Tracking

- Status: Open — belongs to the legacy-agent retirement PR, not a follow-up
- Branch: `chore/retire-legacy-timeboxing-agent`

## Why

`TimeboxingFlowAgent` and the 34 modules only it reached are gone (55 source
files / 18,503 lines in the first cut, plus `adapters/calendar/models.py`,
`constraint_review.py`, `tool_result_models.py`, and the `ConstraintStore`
session-store class in the two follow-up cuts). `tests/README.md`'s "What's
out" section, two root docs, and four package READMEs still describe pieces
of that code as live, or describe backends it owned as still belonging to it.

## Scope

**1. `tests/README.md` — "What's out of `tests/unit/`" (already partly done).**

A second paragraph naming the 2026-09-09 retirement, the 34 deleted modules,
the ~60 test files that went with them, and the 2 that were rewired instead
was added directly by this PR (the same session that filed this ticket) —
confirm it reads correctly and extend it if the retirement PR's final file
count differs from what's there. The doubles list is unchanged — this
retirement touched no shared test double.

**2. `CALENDAR_QUERY_LOCATIONS.md` and `MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md`
(the two root docs `tests/README.md` already marks superseded).**

Both already carry a "Superseded (2026-09)" note pointing at
`fateforger/haunt/` in place of `CalendarHaunter`. Add one line to each
saying `McpCalendarClient` (`fateforger.agents.timeboxing.mcp_clients`) is
also gone now — it left with the legacy agent — and that `src/tmbx/calendar/`
(`port.py`, `gcal.py`, `fake.py`) is the calendar port on the surviving path.
Do not rewrite anything else in either file; they stay a record of what was
true when written.

**3. `src/fateforger/agents/timeboxing/README.md` and `AGENTS.md`.**

Both describe the Graphiti-backed durable memory and the `constraint_mcp`
backend as belonging to the timeboxing agent — read the "Graphiti durable
memory cutover" status row, the `graphiti_constraint_memory.py` /
`constraint_record_memory.py` file-index entries, and `AGENTS.md`'s
"Durable constraint retrieval is centralized in `constraint_retriever.py`"
invariant. `constraint_retriever.py`, `constraint_search_tool.py`, and
`notion_constraint_extractor.py` are deleted; the surviving durable-memory
backends (`graphiti_constraint_memory.py`, `constraint_record_memory.py`,
`kg_constraint_client` → `durable_constraint_store` →
`DeepSeekTimeboxPlanner`) are read through `settings.timeboxing_memory_backend`
by `runtime.py`'s graphiti startup checks and by tasks' defaults memory now,
not by the (deleted) coordinator. Rewrite the status table row, the file
index entries, and the `AGENTS.md` invariant to say the backends are the
tasks-defaults-memory path's, not the timeboxing agent's own — and remove or
correct any surviving reference to `constraint_retriever.py`,
`constraint_search_tool.py`, or `notion_constraint_extractor.py` as if they
still exist. Two concrete hits to fix while in these files: `README.md`'s
`mcp_clients.py` file-index row still names `McpCalendarClient` as a live
export (it's deleted; `mcp_clients.py` now holds only `ConstraintMemoryClient`);
`AGENTS.md`'s "Framework First" section cites `nodes/nodes.py` (the whole
`nodes/` package is deleted) as an example of `GraphFlow`/`DiGraphBuilder`
usage — replace the citation or drop it.

**4. `src/fateforger/core/README.md`.**

Same correction as item 3, scoped to this file's own claim: it currently
reads as if `TIMEBOXING_MEMORY_BACKEND=graphiti` and the Graphiti startup
checks exist for the timeboxing agent. Say they serve `runtime.py`'s startup
checks and tasks' defaults memory now.

**5. `src/fateforger/slack_bot/README.md`.**

Commit `6f93212` (this retirement) deleted the `constraint_review.py` file-
index row and the `timeboxing_constraint_review` /
`ff_timeboxing_constraint_review_all` action-id rows without replacement
text — the modal-based constraint review surface those rows described is
gone (its only writers were the legacy agent and a review modal only it
posted). Add one line where those rows were, saying the surface is gone and
naming why (no writer left after the retirement), rather than leaving the
removal silent.

## Out of scope

Deleting any further dead code the retirement exposed but did not itself
require deleting (`llm/factory.py`'s `calendar_submitter`/`timebox_patcher`
branches, `tmbx/journal/store.py`'s `journal_sessionmaker`) — that is a
source change with its own follow-up, not a docs change.

`docs/superpowers/research/` and `docs/superpowers/plans/` — dated records
of what was true when written; a path that has since moved is not an error
in them.

## Done when

- `tests/README.md`'s "What's out" section names the retirement and no
  longer only lists the earlier six-file prune;
- the two root calendar docs name `McpCalendarClient`'s removal and
  `tmbx/calendar/` as its replacement;
- `src/fateforger/agents/timeboxing/README.md`, its `AGENTS.md`, and
  `src/fateforger/core/README.md` describe the graphiti/constraint-memory
  backends as serving tasks' defaults memory and `runtime.py`'s startup
  checks, not the (deleted) timeboxing coordinator;
- `src/fateforger/slack_bot/README.md` explains, rather than silently
  omits, the two rows Task 6 removed;
- `grep -rn "TimeboxingFlowAgent\|McpCalendarClient\|constraint_review\|timeboxing_submit\|nodes/nodes" --include="*.md" src tests README_CALENDAR_MCP.md GOOGLE_CALENDAR_MCP_GUIDE.md`
  returns only lines that say the thing is gone;
- the docs commit is part of this PR.
