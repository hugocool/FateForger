# 📋 Ticket: Recover the two lines the legacy retirement left untested

## Tracking

- Status: Open — not blocking
- Branch: `chore/retire-legacy-timeboxing-agent`

## Why

The legacy-agent retirement's coverage diff (`scripts/dev/tests/covdiff.py`
against PR #396, Task 7, corrected in the 2026-09-09 fix wave — see
`.superpowers/sdd/2026-09-09-retire-legacy-timeboxing-agent/task-7-report.md`
§3) found several surviving files that lost covered lines a now-deleted
test used to reach. Most of those turned out to be dead code the
retirement exposed rather than caused: `agents/schedular/models/calendar.py`
and `agents/schedular/models/core.py:143`'s only consumer,
`CalendarEventWorkerAgent` (`agents/schedular/agent.py:~901`), is never
registered in `runtime.py`; and most of
`agents/timeboxing/durable_constraint_store.py`'s lost lines
(325-329, 334-336, 357) are `get_store_info` (zero callers) and
`callable(...)` fallbacks the live client never takes.

Two lines are not that shape — they are ordinary-day code paths that live
callers do reach, now with no test exercising them:

1. `src/fateforger/contracts.py:51` — `EventDateTime._parse_date`'s string
   branch. An all-day Google Calendar event arrives as `{"date": "2026-09-09"}`
   rather than `{"dateTime": ...}`; `src/fateforger/haunt/reconcile.py:1036`
   imports `EventDateTime` from `contracts` and depends on this parse to
   resolve the event's date. This is not a rare edge case — every all-day
   event on the calendar takes this path.
2. `src/fateforger/agents/timeboxing/durable_constraint_store.py:506` —
   inside `ClientBackedDurableConstraintStore.find_equivalent_constraints`,
   the `if not rows and search_names:` re-query: when the first
   `query_constraints` call (scoped by a `text_query` built from
   `search_names`) returns no rows, it re-queries once more with
   `require_active: False` and no `text_query` — the ordinary "the
   narrow text search matched nothing, widen it" path, not a rare failure
   mode.

## Scope

Two tests, one per line:

1. **`contracts.py:51`.** Construct an `EventDateTime` (or the model that
   embeds it) with `date="2026-09-09"` (a string, as Google's calendar API
   sends an all-day event) and assert the parsed `.date` field is
   `date(2026, 9, 9)` — not a passthrough of the raw string. File:
   `tests/unit/core/test_contracts.py` (create if it does not exist) or
   alongside `contracts.py`'s existing tests if a file already covers other
   `EventDateTime` fields.
2. **`durable_constraint_store.py:506`.** Build a `ClientBackedDurableConstraintStore`
   over a fake/double client whose `query_constraints` returns `[]` on its
   first call and a non-empty list on its second, then call
   `find_equivalent_constraints(records=[...])` with at least one record
   that has a `name` (so `search_names` is non-empty). Assert the result
   reflects the second call's rows, and that the client's second call's
   `filters` omitted `text_query` (present on the first call, dropped on
   the retry). File: `tests/unit/timeboxing/test_durable_constraint_store.py`
   (create if none exists) or the nearest existing test module for this
   class.

## Out of scope

- `debug/diag.py:46-48` (the `except Exception` arm of `with_timeout`) —
  also a real regression per the same coverage diff, but not part of this
  ticket; it is an accepted known gap noted in the PR body.
- Deleting the dead-code lines this same diff exposed
  (`agents/schedular/models/calendar.py`, `core.py:143`,
  `durable_constraint_store.py:325-329, 334-336, 357`, the two
  `llm/factory.py` branches, `tmbx/journal/store.py`'s
  `journal_sessionmaker`) — a source change with its own follow-up, not a
  test-recovery change.

## Done when

- The two tests above exist, pass, and each fails if its target line is
  reverted to the pre-fix (i.e. untested) behavior;
- `PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/core/test_contracts.py tests/unit/timeboxing/test_durable_constraint_store.py -q`
  (or wherever the tests actually land) passes.
