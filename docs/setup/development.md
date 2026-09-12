---
title: Development
---

Run `./setup-dev.sh` then `make dev` to enter the container. Use `./run.sh` to
start all agents with environment from `.env`.

## Material links {#material-links}

tmbx blocks can carry a link to a work ticket. See
[Materials and Work Links](../architecture/materials-and-work-links.md) for
what that means; this section is only the operational side.

- **Restart the tmbx server after this change lands**, so its startup call to
  `init_journal()` (`src/tmbx/journal/store.py`) adds the `materials` table
  to the existing `data/tmbx_journal.db`. The call is idempotent — it only
  creates tables that are missing — but it only runs on startup, so the
  server has to actually restart for the table to appear. Under the demo
  supervisor that is `./.venv/bin/python scripts/demo.py restart tmbx`.
- **Expect one visible change on existing events.** `extendedProperties.private`
  merges rather than replaces on `update-event`, so a stale key from before
  this change (a removed slug, an old description) could still be sitting on
  an event. The first new-code write to such an event finally clears it,
  because every key is now sent on every write, empty string standing for
  absence. That is the fix working, not a regression to chase.
- **A wiped or relocated `data/tmbx_journal.db` strands every committed day
  that carries a link.** `_dead_link_violation` (`src/tmbx/service.py`)
  refuses to commit a day while any of its blocks point at a handle the
  material store no longer holds. The refusal names the block and the
  handle; clear the link on that block with an explicit null, or restore the
  ticket under the same handle, before the day can be committed again.

## A session-store outage refuses every timeboxing turn {#session-store-outage}

**Expected, and it will not look expected.** When the timeboxing session store
cannot be read, the referent rung (`src/fateforger/slack_bot/handlers.py`)
cannot draw the catalog of days that already stand, and every door that would
hand a turn to `timeboxing_agent` refuses with:

> :warning: I couldn't check what you already have planned, so I won't start a
> second session over the top of it. Say that again and I'll retry, or open one
> from the day's own thread if there is one.

That includes **continuing a session that really does stand**. The guard asks
the store whether a row exists at the key the turn would use
(`_a_partial_catalog_forbids_this_turn`), and the store is the thing that is
down, so "there is nothing there" and "I could not tell" have to answer the
same way. Fail-closed is deliberate: the alternative is a second five-stage
session opened over a day that was already committed, which is the 2026-09-05
incident this rung exists to close.

What it looks like on call, and what to do:

- **Symptom.** Every planning message — new day, DM, a reply in a live session
  thread — comes back with the line above. Nothing else in the bot is affected:
  the receptionist still answers, and a message not headed for a session runs
  normally. A bot answering some things and refusing all planning is the
  signature; a total outage is not.
- **Confirm it is the store.** `record_error(component="surface_intent")` fires
  with `error_type="referent_catalog_partial"` on every refusal, and
  `component="referent_catalog"`, `error_type="provider_failure"` on the read
  that failed. The exception itself is logged by `build_catalog`.
- **Fix.** The store is the sessions database behind
  `SqlAlchemyTimeboxingSessionRepository`; restore it or its connection. The
  refusal clears by itself on the next message once reads succeed — there is no
  cache to bust and no flag to reset.
- **Do not** work around it by restarting the bot or clearing focus. Neither
  touches the store, and `/ff-clear` drops a focus binding without dropping the
  redirect beside it, which is a separate hazard the same guard covers.
