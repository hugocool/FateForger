# Fix: PlannerAgent's MCP workbench never recovered from a transport blip

## Incident

The `google-calendar-mcp` container crash-looped from 09:13 (a zero-byte
`tokens.json`). The Slack bot started at 11:24 while it was down. Every "Add
to calendar" press then failed with `MCP Actor not running, call
initialize() first`, including "Try again", and it stayed broken even after
the server came back — only a bot restart fixed it.

## Root cause

`PlannerAgent._ensure_workbench` cached one `McpWorkbench` for the process
lifetime with no health check and no reset. AutoGen's
`McpWorkbench.call_tool` only calls `start()` when `self._actor` is falsy;
once an actor exists but its session has died, every later call raises `MCP
Actor not running` forever, and nothing in `PlannerAgent` ever discarded that
dead actor. `McpCalendarClient` (timeboxing) had already solved exactly this
with `_RECOVERABLE_ERROR_MARKERS` / `_is_recoverable_transport_error` /
`_reset_workbench` / a retry-once loop — `PlannerAgent` simply never got the
same treatment.

## What changed

1. **`src/fateforger/core/mcp_transport.py` (new).** Pulls the recoverable-
   transport-error markers and `is_recoverable_transport_error(exc)` out of
   `McpCalendarClient` into one shared, pure, offline-testable module so the
   two clients cannot drift apart on what counts as recoverable. No I/O, no
   network, no model.

2. **`src/fateforger/agents/timeboxing/mcp_clients.py`.** `McpCalendarClient`
   now imports and delegates to `is_recoverable_transport_error` instead of
   keeping its own private `_RECOVERABLE_ERROR_MARKERS` tuple and matching
   logic. `_is_recoverable_transport_error` is kept as a thin classmethod
   wrapper so nothing else in the class had to change. Behaviour is
   unchanged — same markers, same matching, same call sites.

3. **`src/fateforger/agents/schedular/agent.py`.** `PlannerAgent` gained:
   - `_reset_workbench()`: discards the cached (possibly dead) workbench so
     the next `_ensure_workbench()` call builds a fresh one. Best-effort
     calls `.stop()` on the old one if present, swallowing any error from an
     already-broken actor.
   - `_call_tool_with_retry(tool_name, arguments, *, retry: bool)`: calls a
     tool on the current workbench; on a non-recoverable error it re-raises
     immediately (no reset, no retry — the server was reachable and said
     no); on a recoverable transport error it always resets the workbench,
     then either retries once (`retry=True`) or re-raises without resending
     (`retry=False`).

   All six `await workbench.call_tool(...)` sites inside `PlannerAgent` (the
   original lines 478, 532, 548, 600, 632, 669) now go through this method.
   Line 898 (`call_create_event_with_retry`, a module-level function taking
   a `workbench` argument for the separate `CalendarEventWorkerAgent`) was
   left untouched — see "What I deliberately did not touch" below.

## Write-safety decision

Reads (`list-events`, `get-event`) are retried (`retry=True`): resending a
read cannot create a duplicate side effect, so this is the case the incident
was actually about — a "Try again" click doing a `get-event`/`list-events`
should just work once the workbench resets.

`delete-event` (cleaning up an already-cancelled event before recreating it)
is also retried (`retry=True`): deleting is idempotent — resending it either
succeeds again or 404s on an already-deleted event, and that 404 path is
already handled by the surrounding `try/except` that logs a warning and
falls through to the create path. It can never produce a duplicate.

`create-event` / `update-event` (the main upsert call, and its
already-exists fallback update) are **not** retried (`retry=False`). The
reasoning:

- `MCP Actor not running` is raised locally, in the client, *before any
  request reaches the server* — so when that specific error fires, the
  first attempt is known not to have happened server-side, and a retry
  would be safe on that basis alone.
- But `is_recoverable_transport_error` also matches other markers — a
  response timeout (`"timed out while waiting for response to
  ClientRequest"`), a dropped connection (`"server disconnected"`) — and
  those *can* fire after the request already left the client. For those,
  whether the create reached the server is genuinely unknown.
- The two markers live behind one boolean and one call site can't tell them
  apart without adding a second axis of exception classification that
  doesn't exist yet. Given that ambiguity, and that a wrong call here writes
  a duplicate event to Hugo's real calendar, I chose the conservative
  reading: never auto-resend a mutating calendar call.
- Resetting the workbench still happens on every recoverable error
  regardless of `retry`, so the production bug — the workbench staying dead
  forever, so even "Try again" fails — is still fixed for creates: the
  *next* press (a fresh `handle_upsert_calendar_event` call) gets a working
  workbench and calendar. This was the actual failure mode in the incident;
  it does not require retrying the mutating call itself, only recovering the
  connection for the next attempt.

This decision is locked by
`test_retry_false_resets_the_workbench_but_never_resends_the_call` and
`test_upsert_calendar_event_does_not_blindly_retry_a_create` — both fail if
a create is ever wired through with `retry=True`.

## What I deliberately did not touch

`call_create_event_with_retry` (module-level function, ~line 898) belongs to
the separate `CalendarEventWorkerAgent`, not `PlannerAgent`. It takes its own
`workbench` argument, retries based on `result.is_error` (tenacity,
3 attempts) rather than on a caught exception, and isn't part of the actor-
caching bug this incident was about (`CalendarEventWorkerAgent` builds its
own `McpWorkbench` in `__init__` and never routes through
`PlannerAgent._ensure_workbench`). The task allowed leaving it alone unless
it could be folded in cleanly without changing its signature; folding it in
would mean either changing its signature (it has no access to
`is_recoverable_transport_error`/`_reset_workbench`, which are instance
methods on a different class) or duplicating the reset logic on a bare
function, neither of which is clean. Left as is.

## Tests

New files:
- `tests/unit/test_mcp_transport.py` — the shared classifier, in isolation.
- `tests/unit/test_planner_agent_workbench_retry.py` — `PlannerAgent`'s
  retry helper and its wiring into the real call sites. Covers, each
  confirmed to fail before the corresponding implementation existed:
  - a recoverable error causes exactly one reset and one retry, and the
    second attempt's result is returned;
  - a non-recoverable error propagates immediately with no reset and no
    retry;
  - the retry budget is one: two consecutive recoverable errors raise
    rather than looping (asserted via exact call/reset counts);
  - `retry=False` still resets the workbench but never resends the call
    (the write-safety lock);
  - an end-to-end check that `handle_suggest_next_slot` actually recovers
    through the real `list-events` call site;
  - an end-to-end check that `handle_upsert_calendar_event` does not
    blindly retry a `create-event` after a recoverable failure, and that
    the dead workbench still gets discarded.

All six were run and observed failing (`AttributeError:
'PlannerAgent' object has no attribute '_call_tool_with_retry'`, or the real
exception surfacing unhandled) before the implementation was added, per the
task's requirement.

## Test command and output

```
PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/ -k "planner or calendar or mcp or schedular" -q -m "not slow"
```

```
============================= test session starts ==============================
platform darwin -- Python 3.11.9, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/hugoevers/VScode-projects/admonish-1/.claude/worktrees/planner-mcp-retry
configfile: pyproject.toml
plugins: langsmith-0.12.1, mock-3.15.1, cov-7.1.0, httpx-0.30.0, anyio-4.15.0, asyncio-0.21.2
asyncio: mode=Mode.AUTO
collected 3501 items / 2911 deselected / 590 selected

... (584 passed, 6 skipped, unrelated pre-existing skips) ...

=============== 584 passed, 6 skipped, 2911 deselected in 14.32s ===============
```

Also ran in isolation:

```
PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/test_planner_agent_workbench_retry.py tests/unit/test_mcp_transport.py -q
============================== 18 passed in 0.09s ==============================
```

## Concerns / follow-ups

- `_reset_workbench`'s best-effort `.stop()` call swallows all exceptions
  from the dying actor. That mirrors `McpCalendarClient`'s existing
  `_reset_workbench`, which doesn't even attempt `.stop()` because
  `McpWorkbench` has no `close` attribute (it only skips closing, it never
  calls `.stop()` either). I chose to attempt `.stop()` best-effort in
  `PlannerAgent` since `McpWorkbench` does expose it, but if that stop
  itself resource-leaks under a truly dead actor, that would need separate
  investigation — out of scope here.
- The ambiguity between "actor never started, definitely pre-request" and
  "timeout/disconnect, possibly mid-request" inside a single
  `is_recoverable_transport_error` boolean is a real limitation: a more
  precise fix would split the classifier into "definitely pre-request" vs
  "ambiguous", and only auto-retry creates on the former. I did not do that
  here because it goes beyond what either client currently distinguishes.
  I checked: `McpCalendarClient._call_tool_payload` (the retry-once wrapper
  this whole design is modelled on) is, in production, only ever invoked by
  `list_day_snapshot` against `list-events` — a read. It has never actually
  exercised its retry-once path against a write. So `PlannerAgent` refusing
  to auto-retry `create-event`/`update-event` isn't a deviation from prior
  art; it's the first time this pattern is applied to a write at all, and I
  erred conservative rather than assume the existing retry logic was ever
  validated against that case. Splitting the classifier remains a genuine
  follow-up, not a decision I'm confident closes the topic.
