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
  server has to actually restart for the table to appear.
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
