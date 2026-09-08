# 📋 Ticket: Bring the test docs back in line with the pruned suite

## Tracking

- Status: Open — belongs to the prune/consolidate PR, not a follow-up
- Branch: `chore/prune-and-consolidate-tests`

## Why

The unit suite moved from 300 flat files into subject subpackages, six
dead-subject test modules were deleted, and 20 fragment files were folded into
their subjects. Three kinds of documentation now disagree with the tree.

## Scope

**1. `tests/README.md` — rewrite (the main job).**

It carries a hand-maintained per-file index with a row per test module. That
index is why it rots: every new test file is a doc edit nobody makes, and it
already lists `test_timeboxing_flow.py` as "Legacy flow logic" — a file whose
subject has had no caller since February.

Replace the index with the shape, not the inventory:

- the subpackage layout (`tests/unit/{timeboxing,slack,constraints,core,haunt,
  tasks,schedular,tmbx}/`) and what belongs in each;
- `tests/doubles/` — the rule that a double used by two modules lives there
  rather than being imported out of whichever test file defined it first, and
  why (importlib mode makes that import depend on collection order);
- `tests/repo.py` — find the repo root, never count `parents[N]`;
- how to run things. **Check the commands: the README says `poetry run pytest`,
  and the suite is currently run with the venv directly. Say what actually
  works.**

A reader should be able to place a new test without reading the file list.

**2. Root-level docs that describe deleted code as production.**

`CALENDAR_QUERY_LOCATIONS.md` marks `CalendarHaunter` "✅ PROD" and
`MIGRATION_ARCHIVE_TO_CALENDAR_HAUNTER.md` recommends migrating *to* it.
Nothing has constructed it since haunting moved to `fateforger/haunt/`; its
tests are deleted in this branch. Do not silently rewrite the history — mark
both as superseded, say what replaced them, and point at `fateforger/haunt/`.

**3. Do not touch** `docs/superpowers/research/` or `docs/superpowers/plans/`.
Those are dated records of what was true when written; a path that has since
moved is not an error in them.

## Out of scope

Deleting the dead modules themselves (`admonisher/{base,calendar,commitment}`,
`schedular/diffing_agent`, `timeboxing/{flow,prompts,state,notebook_entrypoints}`,
`slack_bot/{relay_agent,topics}`, `tools_config/`). That is a source change and
wants its own PR — this one is test-only. The evidence is in the PR body.

## Done when

- `tests/README.md` describes the current tree and names no file that does not
  exist;
- the two root docs no longer present deleted code as production;
- the docs commit is part of this PR.
