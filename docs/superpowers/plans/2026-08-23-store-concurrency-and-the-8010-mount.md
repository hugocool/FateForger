# Writing to the memory store while something holds it open

Tickets: hugocool/FateForger#184 (merged, `68ab5e7`), #185 (fixed, `2b7f45e`),
#186 (open). Date: 2026-08-23. Three sessions, 2026-08-22 into 2026-08-23.

**Why this file exists.** Most of what follows was settled in messages between
sessions that have since run out of context. The conclusions are durable — they
are in a test, two commits and an open ticket — but the *reasoning*, and three
wrong turns that are easy to repeat, were not written down anywhere a later
session would look.

## What was being decided

A reseed wanted to write `data/memory.db` while the MCP server on `:8010` held
it open and the Slack bot ran against it. Nobody knew whether that was safe.

## What is now true, and where it is pinned

**An external process's commit into the same file is visible to an already-open
store, with no reconnect.** Pinned by `tests/memory/test_external_writer.py`.
This is what makes an out-of-band seed safe under a live server.

**Replacing the file is never safe.** The held connection stays on the unlinked
inode. What happens next is not deterministic — either `disk I/O error` or an
answer from cached pages — so only the invariant is asserted: the replacement
never becomes visible. **Replace a store and you must restart whatever holds it
open**, which concretely is:

```sh
./.venv/bin/python scripts/demo.py restart memory
```

`scripts/demo.py status` is the check afterwards: it exits 0 only when every
service is running, serving, **and running the code currently on disk**, which
is the same class of mistake one layer up — a process that looks healthy while
executing something that no longer exists.

**The versioned `memory-readonly-server.py` is now the one that runs.** It was
two independent files; `~/.dsh/profiles/tmbx/` is now a symlink into
`infra/dsh/profile/` (#185, `2b7f45e`). `cordis.patch.yml` stays a copy on
purpose, because the harness rewrites it; `diff -r` is its check.

## Three wrong turns, all of them plausible

**`busy_timeout` looks like it is missing. It is not.** `PRAGMA busy_timeout`
from the `sqlite3` CLI reports `0`, which reads as "unset everywhere". It is
reporting the CLI's own connection. The stores inherit Python's
`sqlite3.connect(timeout=5.0)`, so theirs is 5000ms. A fix for this was drafted
and would have been a no-op.

**"A replaced file fails silently" is too strong.** It was asserted, and the
suite failed it. Do not pin non-deterministic behaviour.

**Comparing row counts is not the same as knowing whose rows they are.** A
backup was called stale because live had 35 more observations than it. All 35
carried one session id — they *were* the change being backed out. `group by
session_id` answered in one query what a count could not.

These are one shape, not three, and naming it is worth more than the three
instances: **an instrument that is measuring itself, and looks credible doing
it.** The CLI reports its own connection's timeout. The count describes the
file rather than the change. A timing harness on this stack was separately
found reporting its own poll budget as system latency. In each case the reading
is real, precise, and about the wrong subject — so it survives a sanity check.
Ask what the instrument is attached to before believing the number.

## What the "readonly" server taught, beyond the fix

`memory-readonly-server.py` had `memory_observe` in `ALLOWED` and
`MEMORY_DB_PATH` on the real store, while its docstring said it wrote to a
throwaway copy. An open write path into the live corpus, behind a filename
saying otherwise.

The docstring was corrected first. That was right — it was untrue and people
read it — but it was the symptom. The mechanism was that the running copy was
not the reviewed copy, which is what let the claim and the reality drift apart
in the first place, and it needed the symlink.

## What is still open

**#186 — ingest is not atomic.** Each store opens its own `sqlite3.connect` to
the same file (`store.py:28`, `constraint_store.py:27`, `anchor_store.py:56`),
so `observe()` ingests on one connection and projects on another.

Two symptoms, one cause. On a crash, an observation can be left unprojected —
recoverable, but **only if the caller passes a stable `write_uid`** on retry
(`service.py:83-97`); without one the retry appends a duplicate and inflates
support for whatever failed most often. Under concurrency, a second `observe()`
can interleave between the two steps: the stores each serialise on a
`threading.RLock`, but `MemoryService` holds no lock of its own. The
2026-08-22 reseed ran six concurrent `observe()` calls through it and came out
clean — an observation, not a guarantee.

Whoever consolidates onto one connection fixes both.

**Do not read #184 as blocking that work.** It asserts behaviour, never
`isolation_level`, precisely so the transaction model can be tidied. And note
what it is: `isolation_level` appears nowhere in `src/memory/`, all three stores
pass only `check_same_thread=False`, and `constraint_store.py` commits
explicitly at lines 64 and 85 and via `with self._conn:` at 97. That handling
grew. #184 **establishes** a contract; it does not record a decision. Changing
it is a first decision, not an overturned one.

**#176 looks stale.** Its three known-red tests all pass on `main` as of today.
