# Required Blocks — Increment 0+1 (spike + memory side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Memory can state, structurally, that a block of a registered kind must be on the day; the first registered kind is `planning`, promoted by hand on Hugo's real store; and the #210 spike has answered whether tmbx's private extended properties survive a Google UI edit.

**Architecture:** A new `enforceable_kinds` registry written only by an explicit promotion; a sixth extraction judgement, `requires_block`, that chooses from the registry's slugs and is verified against them; `day_types` finally gets a writer on the tier question; both new fields are projected at ingest, carried by reprojection, and exposed on `ConstraintView`. The read path stays arithmetic. Spec: `docs/superpowers/specs/2026-09-04-required-blocks-design.md` §1.

**Tech Stack:** Python 3.12, pydantic v2, sqlite3, `mcp` FastMCP, pytest (`asyncio_mode=auto` in `tests/memory`), OpenRouter (`google/gemini-3.6-flash`) for evals.

## Global Constraints

- Run everything under `src/memory/` with `PYTHONPATH=src` from the worktree root. Tests: `PYTHONPATH=src uv run pytest tests/memory/... -q -p no:cacheprovider`.
- **No keyword, substring or regex matching on user content, anywhere, tests included** (CLAUDE.md). A slug is validated by *shape* as an identifier the system mints; a model chooses which slug a statement means.
- The read path (`src/memory/read_api.py`) may import only `__future__`, `datetime`, `memory.constraint`, `memory.constraint_store`; it has no `async def` and no `await`. An AST test enforces this. Nothing in this plan touches it.
- Migrations are append-only DDL strings that must be re-runnable (`CREATE ... IF NOT EXISTS`); bump `SCHEMA_VERSION` in the same commit as the new entry and extend `_EXPECTED_COLUMNS`.
- The observation log is append-only (I2). New fields are *derived*: written at projection, re-derived by reprojection, never edited in place on an observation.
- Identity is minted, never derived from content (I3). Registry slugs are minted by a promotion, never by `observe`.
- Every model-dependent test is an **eval**: real model, 8 draws per case, a rate asserted (≥ 7 of 8), marked `slow` and skipped without `OPENROUTER_API_KEY`. Unit tests stub the model and assert plumbing only; never assert a model's exact string.
- `data/memory.db*` is Hugo's real corpus: back it up before any write to it, never commit it.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Work on branch `docs/required-blocks-design` in worktree `.worktrees/required-blocks-design` (rename the branch to `feat/required-blocks-memory` at the first code commit: `git branch -m feat/required-blocks-memory`).

## File structure

| file | responsibility |
|---|---|
| `scripts/spikes/private_props_survive_ui_edit.py` | **new** — #210 spike: write a slugged probe event, re-read it after UI edits, print what survived |
| `src/memory/migrations.py` | schema v4: `enforceable_kinds`, `constraint_required_blocks` |
| `src/memory/kind_store.py` | **new** — `EnforceableKind` model, `EnforceableKindStore` (shared-connection store) |
| `src/memory/constraint.py` | `Constraint.requires_block`, `ConstraintView.requires_block`, `to_view` |
| `src/memory/constraint_store.py` | persist `requires_block` in `constraint_required_blocks` |
| `src/memory/judge.py` | `RequiresBlockJudgement`, `Judge.requires_block`, `TierJudgement.day_types`, `StubJudge` knobs |
| `src/memory/prompts.py` | `REQUIRES_BLOCK_PROMPT`, `day_types` paragraph in `TIER_PROMPT`, `PromptJudge.requires_block` |
| `src/memory/ingest.py` | sixth judgement in the gather; `IngestResult.requires_block`, `.day_types` |
| `src/memory/projection.py` | write both fields on create; `requires_block` moves up on fold |
| `src/memory/reprojection.py` | `_judge_all` asks three; `_derive` carries both fields |
| `src/memory/service.py` | `MemoryService.promote_kind`, kinds threaded into observe/reproject |
| `src/memory/mcp_server.py` | `memory_promote_kind` tool |
| `tests/memory/test_kind_store.py`, `test_requires_block.py`, `test_eval_requires_block.py`, `test_eval_day_types.py` | **new** tests |
| `tests/memory/test_migrations.py`, `test_constraint_store.py`, `test_judge.py`, `test_ingest.py`, `test_projection.py`, `test_reprojection.py`, `test_mcp_server.py` | extended |

---

### Task 0: The #210 spike — do private extended properties survive a Google UI edit?

**Files:**
- Create: `scripts/spikes/private_props_survive_ui_edit.py`
- Modify: `docs/superpowers/specs/2026-09-04-required-blocks-design.md` ("Increments" section: record the result)

**Interfaces:**
- Consumes: `tmbx.calendar.gcal.GoogleCalendarAdapter(tz=...)` with `create(calendar_id, CalendarEvent)`, `list_day(calendar_id, day, tz)`, `delete(calendar_id, event_id)`; `tmbx.calendar.port.CalendarEvent(event_id, summary, start, end, uid=, handle=, slug=, block_type=, timing_mode=, anchor_source=)`. The calendar MCP server must be running (`MCP_CALENDAR_SERVER_URL`, default `http://localhost:3000`), as for `scripts/tmbx_write_smoke.py`.
- Produces: a written answer in the spec. If the answer is "no", **stop and re-decide §4 of the spec with Hugo before Task 1**.

- [ ] **Step 1: Write the spike script**

```python
#!/usr/bin/env python3
"""#210: do tmbx's private extended properties survive a Google Calendar UI edit?

Two phases, run by hand, against a far-future empty day on Hugo's own calendar:

  write   create one probe event carrying tmbx.uid / tmbx.slug / tmbx.type,
          print its id, then STOP. Hugo now edits it in the Google Calendar UI:
          drag to a new time, rename, resize, and "duplicate" it.
  check   re-read the day through the adapter and print, per event, which of
          the three private keys came back. Then delete every probe event.

Decision 4 of docs/superpowers/specs/2026-09-04-required-blocks-design.md rests
on the answer being "all three survive drag, rename and resize". Duplicate is
reported for information: a duplicate that copies the slug is a second block of
the same kind, which the watcher must count, not resolve.

Usage:
    PYTHONPATH=src uv run python scripts/spikes/private_props_survive_ui_edit.py write \
        --calendar-id hugo.evers@gmail.com --day 2030-01-07
    ... edit in the UI ...
    PYTHONPATH=src uv run python scripts/spikes/private_props_survive_ui_edit.py check \
        --calendar-id hugo.evers@gmail.com --day 2030-01-07 [--keep]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date as date_type
from datetime import datetime

from tmbx.calendar.gcal import GoogleCalendarAdapter
from tmbx.calendar.port import CalendarEvent

PROBE_SUMMARY = "tmbx #210 probe (edit me, then run check)"
PROBE_UID = "spike210uid0000000000000000000001"
PROBE_SLUG = "planning"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["write", "check"])
    p.add_argument("--calendar-id", required=True)
    p.add_argument("--day", default="2030-01-07")
    p.add_argument("--tz", default="Europe/Amsterdam")
    p.add_argument("--keep", action="store_true", help="check: do not delete probes")
    return p.parse_args()


async def write(adapter: GoogleCalendarAdapter, calendar_id: str, day: date_type) -> None:
    created = await adapter.create(
        calendar_id,
        CalendarEvent(
            event_id="",
            summary=PROBE_SUMMARY,
            start=datetime(day.year, day.month, day.day, 10, 0),
            end=datetime(day.year, day.month, day.day, 10, 30),
            uid=PROBE_UID,
            slug=PROBE_SLUG,
            block_type="PR",
        ),
    )
    print(f"created id={created.event_id!r} on {day}; slug={PROBE_SLUG!r} uid={PROBE_UID!r}")
    print("Now, in the Google Calendar UI: drag it to 14:00, rename it, resize it to 45m,")
    print("and use 'Duplicate'. Then run the check phase.")


async def check(
    adapter: GoogleCalendarAdapter, calendar_id: str, day: date_type, tz: str, keep: bool
) -> None:
    events = await adapter.list_day(calendar_id, day, tz)
    probes = [e for e in events if e.uid == PROBE_UID or e.slug == PROBE_SLUG or e.summary]
    print(f"{len(events)} event(s) on {day}:")
    for e in events:
        print(
            f"  id={e.event_id!r} {e.start.time()}–{e.end.time()} summary={e.summary!r}\n"
            f"     tmbx.uid={e.uid!r} tmbx.slug={e.slug!r} tmbx.type={e.block_type!r}"
        )
    survived = [e for e in events if e.slug == PROBE_SLUG]
    print(f"\nevents still carrying slug={PROBE_SLUG!r}: {len(survived)}")
    print("Record in the spec: which edits kept uid/slug/type, and whether the duplicate did.")
    if not keep:
        for e in events:
            if e.uid == PROBE_UID or e.slug == PROBE_SLUG:
                await adapter.delete(calendar_id, e.event_id)
                print(f"deleted {e.event_id!r}")


async def main() -> None:
    a = _args()
    adapter = GoogleCalendarAdapter(tz=a.tz)
    day = date_type.fromisoformat(a.day)
    if a.phase == "write":
        await write(adapter, a.calendar_id, day)
    else:
        await check(adapter, a.calendar_id, day, a.tz, a.keep)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Check the adapter's method names before running**

Run: `grep -n "    async def \(create\|list_day\|delete\|update\)" src/tmbx/calendar/gcal.py`
Expected: all four present with the signatures used above (`create(calendar_id, event)`, `list_day(calendar_id, day, tz)`, `delete(calendar_id, event_id)`). If a signature differs, adapt the script to it, not the other way round.

- [ ] **Step 3: Run the write phase** (calendar MCP server running and authorised; ask Hugo to confirm the day `2030-01-07` is empty)

Run: `PYTHONPATH=src uv run python scripts/spikes/private_props_survive_ui_edit.py write --calendar-id hugo.evers@gmail.com --day 2030-01-07`
Expected: `created id=... slug='planning' ...` and the instructions.

- [ ] **Step 4: Ask Hugo to make the four UI edits, then run the check phase**

Run: `PYTHONPATH=src uv run python scripts/spikes/private_props_survive_ui_edit.py check --calendar-id hugo.evers@gmail.com --day 2030-01-07`
Expected: the moved/renamed/resized probe still shows `tmbx.slug='planning'`; the duplicate is reported either way.

- [ ] **Step 5: Record the result in the spec**

Append under "## Increments" in the spec, after the spike paragraph:

```markdown
**Spike result (2026-MM-DD):** drag: <kept/lost>; rename: <kept/lost>; resize: <kept/lost>;
duplicate: <copied slug / did not>. Decision 4 <stands / must be re-decided>.
```

- [ ] **Step 6: Commit**

```bash
git branch -m feat/required-blocks-memory
git add scripts/spikes/private_props_survive_ui_edit.py docs/superpowers/specs/2026-09-04-required-blocks-design.md
git commit -m "spike(tmbx): do private extended properties survive a Google UI edit? (#210)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: Schema version 4 — `enforceable_kinds` and `constraint_required_blocks`

**Files:**
- Modify: `src/memory/migrations.py` (`SCHEMA_VERSION`, `_V4`, `_MIGRATIONS`, `_EXPECTED_COLUMNS`)
- Test: `tests/memory/test_migrations.py`

**Interfaces:**
- Produces: tables `enforceable_kinds(slug PK, anchor_uid, rule_observation_uid, created_at)` and `constraint_required_blocks(constraint_uid PK, slug)`. A table rather than a column on `constraints`, because `ALTER TABLE ADD COLUMN` is not re-runnable and every migration must be (see `apply_migrations`); same reasoning as `_V3`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/memory/test_migrations.py`:

```python
def test_version_four_adds_the_kind_registry_and_the_required_block_link(tmp_path):
    db = str(tmp_path / "m.db")
    ObservationStore(db)
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"enforceable_kinds", "constraint_required_blocks"} <= tables
        kind_cols = {r[1] for r in conn.execute("PRAGMA table_info(enforceable_kinds)")}
        assert kind_cols == {"slug", "anchor_uid", "rule_observation_uid", "created_at"}
        link_cols = {r[1] for r in conn.execute("PRAGMA table_info(constraint_required_blocks)")}
        assert link_cols == {"constraint_uid", "slug"}
    finally:
        conn.close()
    assert _version(db) == 4


def test_a_version_three_store_upgrades_to_four_and_keeps_its_rows(tmp_path):
    """The case nothing exercised before #155: a store older than the code."""
    db = str(tmp_path / "m.db")
    conn = sqlite3.connect(db)
    try:
        from memory.migrations import _MIGRATIONS

        for v in (1, 2, 3):
            conn.executescript(_MIGRATIONS[v])
        conn.execute("PRAGMA user_version = 3")
        conn.execute(
            "INSERT INTO constraints VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("c1", "n", "d", "must", "profile", "proposed", "user", None, "durable",
             "{}", "2026-01-01T00:00:00+00:00", "permanent", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    store = ConstraintStore(db)
    assert _version(db) == 4
    assert [c.uid for c in store.all()] == ["c1"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_migrations.py -q -p no:cacheprovider -k "version_four or version_three"`
Expected: FAIL — `assert 3 == 4` / missing tables.

- [ ] **Step 3: Add the migration**

In `src/memory/migrations.py`, change `SCHEMA_VERSION = 3` to `SCHEMA_VERSION = 4`, and after `_V3` add:

```python
# Version 4 gives memory a way to say "a block of this kind must be on the
# day" (#212). `enforceable_kinds` is the registry of kinds a rule may
# require: a human slug word, the anchor it is about, and the observation that
# stated the rule when the kind was promoted. It is written only by an
# explicit promotion -- never by observe -- which is what makes a slug a minted
# identity rather than a paraphrase (I3).
#
# `constraint_required_blocks` carries the derived field. A table rather than a
# column on `constraints`, for the same reason as _V3: ALTER TABLE ADD COLUMN is
# not re-runnable, and every migration must be.
_V4 = """
CREATE TABLE IF NOT EXISTS enforceable_kinds (
    slug                 TEXT PRIMARY KEY,
    anchor_uid           TEXT NOT NULL,
    rule_observation_uid TEXT NOT NULL,
    created_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS constraint_required_blocks (
    constraint_uid TEXT PRIMARY KEY,
    slug           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_crb_slug ON constraint_required_blocks(slug);
"""

_MIGRATIONS: dict[int, str] = {1: _V1, 2: _V2, 3: _V3, 4: _V4}
```

(Remove the previous `_MIGRATIONS` line.) In `_EXPECTED_COLUMNS` add:

```python
    4: {
        "enforceable_kinds": {"slug", "anchor_uid", "rule_observation_uid", "created_at"},
        "constraint_required_blocks": {"constraint_uid", "slug"},
    },
```

- [ ] **Step 4: Run the migration tests**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_migrations.py -q -p no:cacheprovider`
Expected: all PASS (including the pre-existing ones — `test_a_fresh_store_is_stamped_with_the_current_version` reads `SCHEMA_VERSION`, so it follows the bump).

- [ ] **Step 5: Commit**

```bash
git add src/memory/migrations.py tests/memory/test_migrations.py
git commit -m "feat(memory): schema v4 — the enforceable-kind registry and the required-block link (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `Constraint.requires_block` persisted and exposed on the view

**Files:**
- Modify: `src/memory/constraint.py` (`ConstraintView`, `Constraint`, `to_view`)
- Modify: `src/memory/constraint_store.py` (`upsert`, `_row`)
- Test: `tests/memory/test_constraint_store.py`

**Interfaces:**
- Produces: `Constraint.requires_block: str | None = None`, `ConstraintView.requires_block: str | None = None`. `ConstraintStore.upsert` writes the link row when set and deletes it when `None`; `_row` reads it back.

- [ ] **Step 1: Write the failing tests**

Append to `tests/memory/test_constraint_store.py` (reuse that file's existing constraint-building helper if one exists; otherwise the inline builder below):

```python
def _rule(uid: str, requires_block: str | None) -> Constraint:
    from datetime import datetime, timezone

    from memory.constraint import Applicability, Necessity, Scope, Source, Status
    from memory.models import Tier

    t0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
    return Constraint(
        uid=uid,
        name="planning session",
        description="Every working day has a planning session",
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        created_at=t0,
        last_observed_at=t0,
        requires_block=requires_block,
    )


def test_requires_block_round_trips_and_clears(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_rule("c1", "planning"))
    assert store.get("c1").requires_block == "planning"
    assert store.get("c1").to_view().requires_block == "planning"
    store.upsert(_rule("c1", None))
    assert store.get("c1").requires_block is None
    assert store.get("c1").to_view().requires_block is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_constraint_store.py -q -p no:cacheprovider -k requires_block`
Expected: FAIL — `Constraint` has no field `requires_block` (pydantic `extra` error or `AttributeError`).

- [ ] **Step 3: Add the field to both models**

In `src/memory/constraint.py`, in `ConstraintView` after `frame_slot`:

```python
    # The registered kind of block this rule says must be on the day, or None.
    # A slug from `enforceable_kinds`, so comparing it is equality over an
    # identifier this system minted (#212). `frame_slot` above is the dead
    # predecessor of this idea and is not reused.
    requires_block: str | None = None
```

In `Constraint`, after `frame_slot: str | None = None`:

```python
    #: DEAD FIELD. Never written by anything the KG server minted; kept only so
    #: legacy rows still parse. Do not read it for anything; see requires_block.
    requires_block: str | None = None
```

(Put the DEAD FIELD comment on `frame_slot` and the real docstring on `requires_block`, i.e. move the two comment lines above `frame_slot`, and give `requires_block` the comment from `ConstraintView`.) In `to_view`, add `requires_block=self.requires_block,`.

- [ ] **Step 4: Persist it in the store**

In `src/memory/constraint_store.py` `upsert`, after `self._conn.commit()` and before `self.replace_links(...)`, inside the lock:

```python
            if constraint.requires_block is None:
                self._conn.execute(
                    "DELETE FROM constraint_required_blocks WHERE constraint_uid = ?",
                    (constraint.uid,),
                )
            else:
                self._conn.execute(
                    "INSERT INTO constraint_required_blocks (constraint_uid, slug) "
                    "VALUES (?,?) ON CONFLICT(constraint_uid) DO UPDATE SET slug=excluded.slug",
                    (constraint.uid, constraint.requires_block),
                )
            self._conn.commit()
```

Add a method:

```python
    def required_block_for(self, constraint_uid: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT slug FROM constraint_required_blocks WHERE constraint_uid = ?",
                (constraint_uid,),
            ).fetchone()
        return row["slug"] if row else None
```

In `_row`, add `requires_block=self.required_block_for(row["uid"]),` (the lock is an RLock; `_row` already re-enters for `observations_for`).

- [ ] **Step 5: Run the store and read-path tests**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_constraint_store.py tests/memory/test_read_api.py tests/memory/test_read_ordering.py -q -p no:cacheprovider`
Expected: all PASS (the AST guard still passes: `read_api.py` is untouched).

- [ ] **Step 6: Commit**

```bash
git add src/memory/constraint.py src/memory/constraint_store.py tests/memory/test_constraint_store.py
git commit -m "feat(memory): Constraint.requires_block, persisted and on the view; frame_slot marked dead (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The kind registry store

**Files:**
- Create: `src/memory/kind_store.py`
- Test: `tests/memory/test_kind_store.py`

**Interfaces:**
- Produces:
  ```python
  class EnforceableKind(BaseModel): slug: str; anchor_uid: str; rule_observation_uid: str; created_at: datetime
  class DuplicateKind(ValueError)
  def validate_slug(slug: str) -> str   # returns slug or raises ValueError
  class EnforceableKindStore:
      def __init__(self, db_path, *, conn=None, lock=None)
      def add(self, kind: EnforceableKind) -> str        # raises DuplicateKind
      def get(self, slug: str) -> EnforceableKind | None
      def all(self) -> list[EnforceableKind]
      def slugs(self) -> list[str]
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/memory/test_kind_store.py`:

```python
# tests/memory/test_kind_store.py
"""The registry of enforceable kinds (#212, spec §1).

A kind is a minted identity: written only by a promotion, compared by equality
ever after. The store validates the slug's *shape* -- an identifier this system
owns -- and never its meaning.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.kind_store import (
    DuplicateKind,
    EnforceableKind,
    EnforceableKindStore,
    validate_slug,
)

T0 = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)


def _kind(slug: str = "planning") -> EnforceableKind:
    return EnforceableKind(
        slug=slug, anchor_uid="a" * 32, rule_observation_uid="o" * 32, created_at=T0
    )


def test_a_kind_is_added_read_back_and_listed(tmp_path):
    store = EnforceableKindStore(str(tmp_path / "m.db"))
    store.add(_kind("planning"))
    store.add(_kind("sleep"))
    assert store.get("planning") == _kind("planning")
    assert store.get("missing") is None
    assert store.slugs() == ["planning", "sleep"]
    assert [k.slug for k in store.all()] == ["planning", "sleep"]


def test_a_duplicate_slug_is_refused_not_overwritten(tmp_path):
    store = EnforceableKindStore(str(tmp_path / "m.db"))
    store.add(_kind("planning"))
    with pytest.raises(DuplicateKind):
        store.add(_kind("planning"))
    assert store.slugs() == ["planning"]


@pytest.mark.parametrize("bad", ["", "Planning", "plan ning", "-planning", "planning-", "plan_ning", "plän"])
def test_a_slug_is_a_lowercase_hyphenated_identifier(bad):
    with pytest.raises(ValueError):
        validate_slug(bad)


def test_valid_slugs_pass_unchanged():
    assert validate_slug("planning") == "planning"
    assert validate_slug("morning-routine") == "morning-routine"


def test_the_model_validates_its_slug():
    with pytest.raises(ValueError):
        _kind("Not A Slug")
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_kind_store.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: memory.kind_store`.

- [ ] **Step 3: Implement the store**

Create `src/memory/kind_store.py`:

```python
# src/memory/kind_store.py
"""The registry of enforceable kinds (#212, spec §1).

A kind is what a required-block rule names and what tmbx writes into
`tmbx.slug` at commit, so the watcher can test presence by equality. It is a
minted identity in the I3 sense: only a promotion writes one, `observe` never
does, and the model's only role is to *choose* one from this list when it reads
a rule. The slug is a human word rather than a uid because a person reads it
in the Google Calendar UI and in the journal; the anchor beside it is the join
to the topic graph.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from pydantic import BaseModel, field_validator

from memory.migrations import apply_migrations
from memory.models import as_aware_utc


class DuplicateKind(ValueError):
    """The slug is already registered; kinds are never overwritten."""


def validate_slug(slug: str) -> str:
    """Shape check on an identifier this system mints: lowercase ASCII letters
    and single hyphens, no leading or trailing hyphen. This decides nothing
    about what the word means; a model does that when it picks one."""
    if not slug:
        raise ValueError("a kind slug cannot be empty")
    if slug[0] == "-" or slug[-1] == "-" or "--" in slug:
        raise ValueError(f"kind slug {slug!r} has a leading, trailing or doubled hyphen")
    for ch in slug:
        if not ((ch.isascii() and ch.isalpha() and ch.islower()) or ch == "-"):
            raise ValueError(
                f"kind slug {slug!r} may contain only lowercase ASCII letters and hyphens"
            )
    return slug


class EnforceableKind(BaseModel):
    slug: str
    anchor_uid: str
    rule_observation_uid: str
    created_at: datetime

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, value: str) -> str:
        return validate_slug(value)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return as_aware_utc(value)


class EnforceableKindStore:
    """Shares one connection and lock with the sibling stores, as they do with
    each other; see ConstraintStore.__init__ for why both travel together."""

    def __init__(
        self,
        db_path: str,
        *,
        conn: sqlite3.Connection | None = None,
        lock: threading.RLock | None = None,
    ) -> None:
        if (conn is None) != (lock is None):
            raise ValueError(
                "conn and lock are shared together or not at all; one "
                "without the other lets two stores overlap on one connection"
            )
        self._conn = conn or sqlite3.connect(db_path, check_same_thread=False)
        self._lock = lock or threading.RLock()
        self._conn.row_factory = sqlite3.Row
        apply_migrations(self._conn)

    def add(self, kind: EnforceableKind) -> str:
        with self._lock:
            if self.get(kind.slug) is not None:
                raise DuplicateKind(f"kind {kind.slug!r} is already registered")
            self._conn.execute(
                "INSERT INTO enforceable_kinds (slug, anchor_uid, rule_observation_uid, created_at) "
                "VALUES (?,?,?,?)",
                (kind.slug, kind.anchor_uid, kind.rule_observation_uid, kind.created_at.isoformat()),
            )
            self._conn.commit()
        return kind.slug

    def get(self, slug: str) -> EnforceableKind | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM enforceable_kinds WHERE slug = ?", (slug,)
            ).fetchone()
        return self._row(row) if row else None

    def all(self) -> list[EnforceableKind]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM enforceable_kinds ORDER BY slug"
            ).fetchall()
        return [self._row(r) for r in rows]

    def slugs(self) -> list[str]:
        return [k.slug for k in self.all()]

    @staticmethod
    def _row(row: sqlite3.Row) -> EnforceableKind:
        return EnforceableKind(
            slug=row["slug"],
            anchor_uid=row["anchor_uid"],
            rule_observation_uid=row["rule_observation_uid"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_kind_store.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/memory/kind_store.py tests/memory/test_kind_store.py
git commit -m "feat(memory): EnforceableKindStore — the registry a promotion writes and a rule names (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The sixth judgement — `requires_block` on the judge port and the prompt transport

**Files:**
- Modify: `src/memory/judge.py` (`RequiresBlockJudgement`, `Judge.requires_block`, `StubJudge`)
- Modify: `src/memory/prompts.py` (`REQUIRES_BLOCK_PROMPT`, `PromptJudge.requires_block`)
- Test: `tests/memory/test_judge.py`, `tests/memory/test_envelope_parsing.py`

**Interfaces:**
- Produces:
  ```python
  class RequiresBlockJudgement(BaseModel): slug: str | None = None; rationale: str = ""
  Judge.requires_block(self, observation: Observation, kinds: list[str]) -> RequiresBlockJudgement
  StubJudge(..., requires_blocks: dict[str, str] | None = None)   # text -> slug
  ```
  `PromptJudge.requires_block` returns `RequiresBlockJudgement()` without a model call when `kinds` is empty; otherwise asks, and raises `ValueError` if the returned slug is not in `kinds`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/memory/test_judge.py`:

```python
def test_stub_returns_its_canned_required_kind():
    from memory.judge import RequiresBlockJudgement

    judge = StubJudge(requires_blocks={"a planning session every working day": "planning"})
    result = await_sync(
        judge.requires_block(_obs("a planning session every working day"), ["planning", "sleep"])
    )
    assert result == RequiresBlockJudgement(slug="planning")
    assert await_sync(judge.requires_block(_obs("sleep at 23:00"), ["planning"])).slug is None
    assert ("requires_block", judge.calls[-1][1]) == judge.calls[-1]
```

Append to `tests/memory/test_envelope_parsing.py`:

```python
def test_requires_block_accepts_a_listed_slug_or_null():
    from memory.judge import RequiresBlockJudgement

    assert await_sync(
        _Replies('{"slug": "planning", "rationale": "states a session must exist"}')
        .requires_block(_obs("x"), ["planning", "sleep"])
    ) == RequiresBlockJudgement(slug="planning", rationale="states a session must exist")
    assert await_sync(
        _Replies('{"slug": null, "rationale": "a duration cap"}').requires_block(_obs("x"), ["planning"])
    ).slug is None


def test_requires_block_refuses_a_slug_that_was_not_offered():
    with pytest.raises(ValueError):
        await_sync(_Replies('{"slug": "plan-review"}').requires_block(_obs("x"), ["planning"]))


def test_requires_block_asks_nothing_when_no_kinds_are_registered():
    class _Explodes(_Replies):
        async def complete(self, system: str, user: str) -> str:
            raise AssertionError("must not call the model with an empty registry")

    assert await_sync(_Explodes("").requires_block(_obs("x"), [])).slug is None
```

(If `test_envelope_parsing.py` lacks `_obs`/`await_sync`, copy them from `test_judge.py`: `_obs` builds an `Observation` with `Channel.PLANNING`, `Provenance.OBSERVED`, and `await_sync` is `asyncio.run` over the coroutine.)

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_judge.py tests/memory/test_envelope_parsing.py -q -p no:cacheprovider -k requires_block`
Expected: FAIL — `AttributeError: requires_block` / `ImportError: RequiresBlockJudgement`.

- [ ] **Step 3: Add the judgement model and port method**

In `src/memory/judge.py`, after `DayJudgement`:

```python
class RequiresBlockJudgement(BaseModel):
    """Which registered kind of block, if any, this statement says must be on
    the day (#212).

    `slug` is one of the kinds the caller offered or None -- a closed choice
    over identifiers this system minted, verified by the transport before it
    is returned. Deciding that "end-of-day closure block" is the `planning`
    kind is a judgement about meaning and stays with the model; deciding that
    the answer is *one of the offered words* is set membership and stays here.
    """

    slug: str | None = None
    rationale: str = ""
```

In the `Judge` protocol, after `necessity`:

```python
    async def requires_block(
        self, observation: Observation, kinds: list[str]
    ) -> RequiresBlockJudgement: ...
```

Update the protocol docstring's "Five independent questions" to "Six independent questions". In `StubJudge.__init__` add the parameter `requires_blocks: dict[str, str] | None = None` and `self._requires_blocks = requires_blocks or {}`; add the method:

```python
    async def requires_block(
        self, observation: Observation, kinds: list[str]
    ) -> RequiresBlockJudgement:
        self.calls.append(("requires_block", observation.uid))
        slug = self._requires_blocks.get(observation.text)
        return RequiresBlockJudgement(slug=slug if slug in kinds else None)
```

- [ ] **Step 4: Add the prompt and the transport method**

In `src/memory/prompts.py`, after `NECESSITY_PROMPT`:

```python
REQUIRES_BLOCK_PROMPT = """\
You decide whether a statement says that a block of some kind MUST BE PRESENT
on the day's plan, and if so which of the listed kinds it means.

The kinds are given as short words. Answer with exactly one of them, or null.
Never invent a word that is not in the list: if the statement requires a kind
of block the list does not have, answer null.

"Requires a block" means the rule is about existence: the day is wrong if no
such block is on it. "Every working day has a planning session" requires one.
"Reserve 15-20 minutes at the end of the workday to update the board" requires
one. These do NOT: a rule about how long a block runs ("deep work blocks run
90-120 minutes"), when it sits relative to another ("oats two hours before
gym"), how blocks alternate, or a guardrail on what may be scheduled ("no
meetings before 13:00"). Those describe blocks the day may have; they do not
say the day must have one.

When unsure, answer null. A block wrongly marked required is placed on every
matching day and nagged about when absent; a required block wrongly missed is
visible the first day it is absent and can be stated again.

Respond with JSON only: {"slug": "<one of the listed kinds>"|null, "rationale": "..."}\
"""
```

In `PromptJudge`, after `necessity`:

```python
    async def requires_block(
        self, observation: Observation, kinds: list[str]
    ) -> RequiresBlockJudgement:
        if not kinds:
            # Nothing to choose from, so nothing to ask. A model offered an
            # empty list can only answer null or invent, and inventing is the
            # failure the verification below exists to catch.
            return RequiresBlockJudgement()
        user = (
            f"Statement:\n{json.dumps(observation.text, ensure_ascii=False)}"
            f"\n\nRegistered kinds:\n{json.dumps(kinds, ensure_ascii=False)}"
        )
        payload = await self._ask(REQUIRES_BLOCK_PROMPT, user)
        if "slug" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        judgement = self._build(RequiresBlockJudgement, payload)
        if judgement.slug is not None and judgement.slug not in kinds:
            # Set membership over identifiers this system minted -- explicitly
            # outside the no-matching rule, and the one check that keeps an
            # invented kind out of the store.
            raise ValueError(
                f"judge returned kind {judgement.slug!r} which is not among the "
                f"{len(kinds)} registered kinds offered"
            )
        return judgement
```

Add `RequiresBlockJudgement` to the `from memory.judge import (...)` block at the top of `prompts.py`.

- [ ] **Step 5: Run the judge tests**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_judge.py tests/memory/test_envelope_parsing.py tests/memory/test_openrouter_judge.py -q -p no:cacheprovider`
Expected: all PASS, including `test_stub_satisfies_the_protocol`.

- [ ] **Step 6: Commit**

```bash
git add src/memory/judge.py src/memory/prompts.py tests/memory/test_judge.py tests/memory/test_envelope_parsing.py
git commit -m "feat(memory): the sixth judgement — which registered kind a rule requires, chosen from a closed list and verified (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `day_types` gets a writer on the tier question

**Files:**
- Modify: `src/memory/judge.py` (`TierJudgement.day_types`, `StubJudge`)
- Modify: `src/memory/prompts.py` (`TIER_PROMPT`)
- Modify: `src/memory/ingest.py` (`IngestResult.day_types`, `_ingest`)
- Modify: `src/memory/projection.py` (both `Applicability(...)` constructions)
- Test: `tests/memory/test_ingest.py`, `tests/memory/test_projection.py`

**Interfaces:**
- Produces: `TierJudgement.day_types: list[str]`, `IngestResult.day_types: list[str]`, `StubJudge(..., day_types: dict[str, list[str]] | None = None)`; `project()` writes `applicability.day_types` from the ingest result.

- [ ] **Step 1: Write the failing tests**

Append to `tests/memory/test_ingest.py`:

```python
async def test_ingest_carries_the_day_types_the_tier_judgement_scoped(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(
        tiers={"planning session on working days": Tier.DURABLE},
        day_types={"planning session on working days": ["working"]},
    )
    result = await ingest(_obs("planning session on working days"), judge, store)
    assert result.day_types == ["working"]
```

Append to `tests/memory/test_projection.py`:

```python
async def test_projection_writes_day_types_into_applicability(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    result = IngestResult(
        stored=True, uid="obs-1", tier=Tier.DURABLE, day_types=["working"]
    )
    c = await project(_obs("planning session on working days"), result, StubJudge(), store)
    assert c.applicability.day_types == ["working"]
    assert store.get(c.uid).applicability.day_types == ["working"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_ingest.py tests/memory/test_projection.py -q -p no:cacheprovider -k day_types`
Expected: FAIL — unexpected keyword `day_types`.

- [ ] **Step 3: Add the field along the chain**

`src/memory/judge.py`, `TierJudgement`, after `days_of_week`:

```python
    # Which kinds of day the rule is limited to, from the system-minted
    # vocabulary DayJudgement uses ("working", "weekend", "vacation",
    # "holiday", "sick"). Empty means every kind. This is the writer
    # Applicability.day_types never had: its reader shipped first, and until
    # now the field could only be seeded by hand.
    day_types: list[str] = Field(default_factory=list)
```

`StubJudge`: add parameter `day_types: dict[str, list[str]] | None = None`, `self._day_types = day_types or {}`, and in `tier()` add `day_types=self._day_types.get(observation.text, []),`.

`src/memory/prompts.py`, in `TIER_PROMPT`, after the `start_date / end_date` bullet:

```
- day_types: which kinds of day the rule is limited to, from exactly these
  words: "working", "weekend", "vacation", "holiday", "sick". Only when the
  statement scopes it -- "on workdays", "when I'm on holiday". A rule that
  holds on every kind of day gets an empty list. Weekdays are not a stand-in:
  "Mon-Fri" is days_of_week, "working days" is day_types ["working"].
```

and in the JSON shape line add `"day_types": [...],` after `"days_of_week": [...],`.

`src/memory/ingest.py`: `IngestResult` gains `day_types: list[str] = Field(default_factory=list)` after `days_of_week`; `_ingest` passes `day_types=tier_j.day_types,` in the returned `IngestResult`.

`src/memory/projection.py`: both `Applicability(...)` constructions gain `day_types=ingest_result.day_types,`.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_ingest.py tests/memory/test_projection.py tests/memory/test_judge.py tests/memory/test_applicability_extraction.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/memory/judge.py src/memory/prompts.py src/memory/ingest.py src/memory/projection.py tests/memory/test_ingest.py tests/memory/test_projection.py
git commit -m "feat(memory): the tier question writes day_types, so a rule can say \"on working days\" (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Ingest asks the sixth question; projection writes `requires_block`

**Files:**
- Modify: `src/memory/ingest.py` (`ingest(..., kinds)`, `_ingest`, `IngestResult.requires_block`)
- Modify: `src/memory/projection.py` (create branches set it; fold branch moves it up)
- Test: `tests/memory/test_ingest.py`, `tests/memory/test_projection.py`

**Interfaces:**
- Produces: `ingest(observation, judge, store, *, kinds: list[str] = ())` — the registry's slugs; `IngestResult.requires_block: str | None`. `project()` sets `requires_block` on a *durable* create; a session-tier create leaves it `None`; a fold sets it on the existing constraint only if the existing has `None` (required once, required after — same direction as tier).

- [ ] **Step 1: Write the failing tests**

Append to `tests/memory/test_ingest.py`:

```python
async def test_ingest_offers_the_registered_kinds_and_carries_the_answer(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(
        tiers={"every working day has a planning session": Tier.DURABLE},
        requires_blocks={"every working day has a planning session": "planning"},
    )
    result = await ingest(
        _obs("every working day has a planning session"), judge, store, kinds=["planning"]
    )
    assert result.requires_block == "planning"
    assert ("requires_block", result.uid) in judge.calls


async def test_ingest_with_no_registered_kinds_records_no_requirement(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(requires_blocks={"every working day has a planning session": "planning"})
    result = await ingest(_obs("every working day has a planning session"), judge, store)
    assert result.requires_block is None
```

Append to `tests/memory/test_projection.py`:

```python
async def test_a_durable_rule_carries_its_required_kind(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    result = IngestResult(stored=True, uid="obs-1", tier=Tier.DURABLE, requires_block="planning")
    c = await project(_obs("every working day has a planning session"), result, StubJudge(), store)
    assert c.requires_block == "planning"
    assert store.get(c.uid).to_view().requires_block == "planning"


async def test_a_session_fact_never_carries_a_required_kind(tmp_path):
    """Durable-only (spec decision 10): 'plan tomorrow's session at 17:00' is a
    fact for the planner, not a standing requirement."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    result = IngestResult(stored=True, uid="obs-1", tier=Tier.SESSION, requires_block="planning")
    c = await project(_obs("plan tomorrow's session at 17:00"), result, StubJudge(), store)
    assert c.requires_block is None


async def test_a_fold_sets_the_required_kind_once_and_never_unsets_it(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    existing = _existing(store, "planning session")
    judge = StubJudge(canonical={"we plan every working day": existing.uid,
                                 "planning again": existing.uid})
    folded = await project(
        _obs("we plan every working day"),
        IngestResult(stored=True, uid="obs-1", tier=Tier.DURABLE, requires_block="planning"),
        judge, store,
    )
    assert folded.requires_block == "planning"
    again = await project(
        _obs("planning again"),
        IngestResult(stored=True, uid="obs-2", tier=Tier.DURABLE, requires_block=None),
        judge, store,
    )
    assert again.requires_block == "planning"
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_ingest.py tests/memory/test_projection.py -q -p no:cacheprovider -k "required_kind or registered_kinds"`
Expected: FAIL — unexpected keyword `kinds` / `requires_block`.

- [ ] **Step 3: Thread the kinds through ingest**

In `src/memory/ingest.py`: `IngestResult` gains `requires_block: str | None = None` (after `decay_class`, with the comment "The registered kind this rule says must be on the day; see projection for the durable-only rule."). Change the signatures:

```python
async def ingest(
    observation: Observation,
    judge: Judge,
    store: ObservationStore,
    *,
    kinds: list[str] = (),
) -> IngestResult:
```

pass `kinds=kinds` into both `_ingest` calls; `_ingest` gains `kinds: list[str]` (keyword-only, after `recent`). In the gather add a sixth coroutine `judge.requires_block(observation, list(kinds)),`; unpack as `anchor_j, tier_j, meta_j, dedup_j, necessity_j, requires_j = results`; update the comment "await all five" → "all six"; add `requires_block=requires_j.slug,` to the returned `IngestResult`. Update the docstring "The five judgements" → "The six judgements".

- [ ] **Step 4: Write it at projection**

In `src/memory/projection.py`:
- Session-tier create branch: no change (the field stays `None`).
- Durable create branch (the last `Constraint(...)`): add `requires_block=ingest_result.requires_block,`.
- Fold branch, after the `last_observed_at` update and before `constraint_store.upsert(existing)`:

```python
            # Required once, required after. A later restatement that omits the
            # requirement does not unset it -- the same direction tier moves in,
            # and for the same reason: unsetting on the newest observation would
            # be last-write-wins.
            if existing.requires_block is None and ingest_result.requires_block is not None:
                existing.requires_block = ingest_result.requires_block
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_ingest.py tests/memory/test_projection.py tests/memory/test_concurrent_ingest.py tests/memory/test_idempotent_write.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/memory/ingest.py src/memory/projection.py tests/memory/test_ingest.py tests/memory/test_projection.py
git commit -m "feat(memory): ingest asks which registered kind a rule requires; projection writes it, durable-only, moving up on a fold (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Reprojection carries `requires_block` and `day_types`

**Files:**
- Modify: `src/memory/reprojection.py` (`_Judged`, `_judge_all`, `_derive`, `reproject`, `split`)
- Test: `tests/memory/test_reprojection.py`

**Interfaces:**
- Produces: `_judge_all(observations, judge, kinds: list[str])`; `reproject(observation_store, constraint_store, judge, *, kinds: list[str] = (), uid=None, apply=False)`; `split(..., kinds: list[str] = ())`. `_derive` yields `requires_block` (any observation's answer, newest first) and `applicability.day_types` from the newest tier judgement that names any, else carried from the existing constraint.

- [ ] **Step 1: Write the failing tests**

Append to `tests/memory/test_reprojection.py` (uses that file's `_stores`, `_observe`, `_stale_constraint`, `WHEN`):

```python
async def test_reprojection_gives_an_old_rule_its_required_kind(tmp_path):
    """The #212 done-criterion: a judgement improvement reaches rows that already exist."""
    obs_store, c_store = _stores(tmp_path)
    obs = _observe(obs_store, "every working day has a planning session")
    stale = _stale_constraint(c_store, obs)
    judge = StubJudge(
        tiers={obs.text: Tier.DURABLE},
        requires_blocks={obs.text: "planning"},
        day_types={obs.text: ["working"]},
    )
    preview = await reproject(obs_store, c_store, judge, kinds=["planning"])
    assert preview.applied is False
    assert set(preview.changed[0].fields) >= {"requires_block", "applicability"}
    assert c_store.get(stale.uid).requires_block is None, "preview writes nothing"
    await reproject(obs_store, c_store, judge, kinds=["planning"], apply=True)
    after = c_store.get(stale.uid)
    assert after.requires_block == "planning"
    assert after.applicability.day_types == ["working"]


async def test_reprojection_keeps_hand_seeded_day_types_when_the_judgement_names_none(tmp_path):
    obs_store, c_store = _stores(tmp_path)
    obs = _observe(obs_store, "sleep at 23:00")
    stale = _stale_constraint(
        c_store, obs, applicability=Applicability(day_types=["working"])
    )
    judge = StubJudge(tiers={obs.text: Tier.DURABLE})
    await reproject(obs_store, c_store, judge, apply=True)
    assert c_store.get(stale.uid).applicability.day_types == ["working"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_reprojection.py -q -p no:cacheprovider -k "required_kind or hand_seeded"`
Expected: FAIL — unexpected keyword `kinds`.

- [ ] **Step 3: Carry both fields**

In `src/memory/reprojection.py`:

`_Judged`:

```python
class _Judged(NamedTuple):
    tier: object
    necessity: object
    requires_block: object
```

`_judge_all(observations, judge, kinds)`: three factories per observation —

```python
            for factory in (
                lambda o=observation: judge.tier(o),
                lambda o=observation: judge.necessity(o),
                lambda o=observation: judge.requires_block(o, list(kinds)),
            )
```

and the result mapping becomes `_Judged(tier=results[i * 3], necessity=results[i * 3 + 1], requires_block=results[i * 3 + 2])`. Update the docstring: "Three of the seven are re-asked" (anchors, meta, dedup, canonicalise stay excluded for the reasons already stated).

`_derive`: replace the `carried_day_types` block and the applicability loop with:

```python
    # day_types now has a writer (the tier judgement), so take it from the
    # newest observation that names any; carry the existing constraint's value
    # only when no judgement does. Before the writer existed this was carried
    # unconditionally, because rebuilding from a judgement that could not
    # express it silently unscoped 22 of 33 constraints on the real store.
    carried_day_types = list(existing.applicability.day_types) if existing else []
    judged_day_types = next(
        (list(judgements[o.uid].tier.day_types) for o in reversed(ordered)
         if judgements[o.uid].tier.day_types),
        carried_day_types,
    )

    applicability = Applicability(day_types=judged_day_types)
    for observation in reversed(ordered):
        judgement = judgements[observation.uid].tier
        if judgement.start_date or judgement.end_date or judgement.days_of_week:
            applicability = Applicability(
                start_date=judgement.start_date,
                end_date=judgement.end_date,
                days_of_week=judgement.days_of_week,
                day_types=judged_day_types,
            )
            break
```

and add to `derived`:

```python
        # Required once, required after: the newest observation that names a
        # kind wins, and one that names none does not unset it.
        "requires_block": next(
            (judgements[o.uid].requires_block.slug for o in reversed(ordered)
             if judgements[o.uid].requires_block.slug is not None),
            None,
        ),
```

`reproject(...)` and `split(...)` gain keyword-only `kinds: list[str] = ()` and pass `kinds` to `_judge_all`. (`split` calls `_judge_all`/`_derive` for both halves — pass it through wherever `_judge_all` is called.)

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_reprojection.py tests/memory/test_decay_projection.py -q -p no:cacheprovider`
Expected: all PASS. Existing tests that construct `_Judged(...)` by hand or call `_judge_all(observations, judge)` need the new argument — update those call sites to pass `kinds=[]` / a three-field `_Judged`.

- [ ] **Step 5: Commit**

```bash
git add src/memory/reprojection.py tests/memory/test_reprojection.py
git commit -m "feat(memory): reprojection carries requires_block and day_types onto rules that already exist (#212, I4)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `MemoryService.promote_kind` and the `memory_promote_kind` tool

**Files:**
- Modify: `src/memory/service.py` (`__init__` gains the kind store; `observe`, `reproject`, `split_constraint` pass kinds; `promote_kind`; `PromoteOutcome`)
- Modify: `src/memory/mcp_server.py` (`register_tools`: `memory_promote_kind`; `INSTRUCTIONS` paragraph)
- Test: `tests/memory/test_mcp_server.py`, new `tests/memory/test_requires_block.py`

**Interfaces:**
- Produces:
  ```python
  class PromoteOutcome(BaseModel): slug: str; anchor_uid: str; anchor_created: bool; constraint_uid: str | None; requires_block_recorded: bool
  async def MemoryService.promote_kind(self, slug: str, *, anchor_name: str, rule_text: str, observed_at: datetime) -> PromoteOutcome
  ```
  MCP tool `memory_promote_kind(slug: str, anchor_name: str, rule_text: str) -> dict`.
  Order inside `promote_kind`: validate + refuse duplicate → resolve anchor → **register the kind** → observe the rule (so the sixth judgement can pick the new slug) → if observe raises, delete the kind row and re-raise.

- [ ] **Step 1: Write the failing tests**

Create `tests/memory/test_requires_block.py`:

```python
# tests/memory/test_requires_block.py
"""Promotion: the one write that mints an enforceable kind (spec §1).

Asks-first lives in the host; here the server's half is asserted: a kind is
registered, its anchor resolved through the judge, the rule stated as a
durable observation, and the projected rule carries the kind. Never minted by
observe: the kind row exists only because promote_kind wrote it.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from memory.judge import StubJudge
from memory.kind_store import DuplicateKind
from memory.models import Tier
from memory.service import MemoryService

T0 = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
RULE = "Every working day has a planning session in which the next day is timeboxed."


def _judge() -> StubJudge:
    return StubJudge(
        tiers={RULE: Tier.DURABLE},
        labels={RULE: "Planning session"},
        day_types={RULE: ["working"]},
        requires_blocks={RULE: "planning"},
        anchors={RULE: ["planning session"]},
    )


async def test_promotion_registers_the_kind_and_the_rule_carries_it(tmp_path):
    service = MemoryService(str(tmp_path / "m.db"), _judge())
    outcome = await service.promote_kind(
        "planning", anchor_name="planning session", rule_text=RULE, observed_at=T0
    )
    assert outcome.slug == "planning"
    assert outcome.anchor_created is True
    assert outcome.requires_block_recorded is True
    views = service.get_active_constraints(date(2026, 9, 7), day_type="working")
    assert [v.requires_block for v in views if v.uid == outcome.constraint_uid] == ["planning"]
    assert service.kinds() == ["planning"]


async def test_a_rule_observed_before_any_kind_exists_records_no_requirement(tmp_path):
    """observe never mints a kind: with an empty registry the sixth judgement is
    not even asked, and the rule is stored without a required kind."""
    from memory.models import Channel

    service = MemoryService(str(tmp_path / "m.db"), _judge())
    outcome = await service.observe(
        RULE, channel=Channel.PLANNING, session_id="s1", observed_at=T0
    )
    assert outcome.stored is True
    assert service.kinds() == []
    views = service.get_active_constraints(date(2026, 9, 7), day_type="working")
    assert all(v.requires_block is None for v in views)


async def test_a_duplicate_promotion_is_refused_and_writes_nothing(tmp_path):
    service = MemoryService(str(tmp_path / "m.db"), _judge())
    await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    before = len(service.get_active_constraints(date(2026, 9, 7), day_type="working"))
    with pytest.raises(DuplicateKind):
        await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert len(service.get_active_constraints(date(2026, 9, 7), day_type="working")) == before


async def test_a_malformed_slug_is_refused_before_anything_is_written(tmp_path):
    service = MemoryService(str(tmp_path / "m.db"), _judge())
    with pytest.raises(ValueError):
        await service.promote_kind("Planning Session", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert service.kinds() == []


async def test_a_failed_observe_unregisters_the_kind(tmp_path):
    class _Fails(StubJudge):
        async def tier(self, observation):
            raise RuntimeError("model down")

    service = MemoryService(str(tmp_path / "m.db"), _Fails(anchors={RULE: ["planning session"]}))
    with pytest.raises(RuntimeError):
        await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert service.kinds() == []
```

Update `tests/memory/test_mcp_server.py::test_the_server_exposes_exactly_the_session_verbs` to include `"memory_promote_kind"`, and append:

```python
async def test_promote_kind_tool_round_trips(tmp_path):
    rule = "Every working day has a planning session in which the next day is timeboxed."
    judge = StubJudge(
        tiers={rule: Tier.DURABLE},
        requires_blocks={rule: "planning"},
        anchors={rule: ["planning session"]},
    )
    server = build_server(_service(tmp_path, judge))
    result = await server.call_tool(
        "memory_promote_kind",
        {"slug": "planning", "anchor_name": "planning session", "rule_text": rule},
    )
    payload = json.loads(result[0].text)
    assert payload["slug"] == "planning"
    assert payload["requires_block_recorded"] is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=src uv run pytest tests/memory/test_requires_block.py tests/memory/test_mcp_server.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: promote_kind` / tool set mismatch.

- [ ] **Step 3: Implement the service method**

In `src/memory/service.py`:

Imports: add `from memory.anchor import Anchor` (only if needed by the resolve path — `resolve_anchors` mints anchors itself, so it is not), `from memory.kind_store import EnforceableKind, EnforceableKindStore`.

`__init__`: after `self._anchors = ...` add `self._kinds = EnforceableKindStore(db_path, conn=conn, lock=lock)`.

Add:

```python
class PromoteOutcome(BaseModel):
    """What a promotion wrote, in terms a host can display."""

    slug: str
    anchor_uid: str
    anchor_created: bool
    constraint_uid: str | None = None
    requires_block_recorded: bool = False
```

Add methods:

```python
    def kinds(self) -> list[str]:
        """The registered enforceable kinds, by slug. No model call."""
        return self._kinds.slugs()

    async def promote_kind(
        self,
        slug: str,
        *,
        anchor_name: str,
        rule_text: str,
        observed_at: datetime,
    ) -> PromoteOutcome:
        """Register a kind of block a rule may require (spec §1, decision 6).

        The one write that mints a kind. `observe` never does: a kind is a
        decision, not a threshold, and the host asks the user before calling
        this. Order matters and is deliberate -- the kind is registered
        *before* the rule is observed so the sixth judgement can choose it,
        and unregistered again if observing fails, so a half-promotion leaves
        no kind nothing states.
        """
        from memory.kind_store import validate_slug

        validate_slug(slug)
        if self._kinds.get(slug) is not None:
            from memory.kind_store import DuplicateKind

            raise DuplicateKind(f"kind {slug!r} is already registered")

        known_before = {a.uid for a in self._anchors.all()}
        (anchor_uid,) = await resolve_anchors([anchor_name], self._anchors, self._judge)
        anchor_created = anchor_uid not in known_before

        observation = Observation(
            text=rule_text,
            channel=Channel.REVIEW,
            provenance=Provenance.OBSERVED,
            session_id=f"promotion:{slug}",
            observed_at=observed_at,
        )
        self._kinds.add(
            EnforceableKind(
                slug=slug,
                anchor_uid=anchor_uid,
                rule_observation_uid=observation.uid,
                created_at=observed_at,
            )
        )
        try:
            result = await ingest(
                observation, self._judge, self._observations, kinds=self._kinds.slugs()
            )
            if not result.stored:
                raise ValueError(
                    f"the promotion's rule was suppressed as {result.suppressed_as!r}; "
                    f"a kind with no rule stating it would be enforced by nothing"
                )
            constraint = await project(
                observation, result, self._judge, self._constraints, self._anchors
            )
        except Exception:
            self._kinds.remove(slug)
            raise
        return PromoteOutcome(
            slug=slug,
            anchor_uid=anchor_uid,
            anchor_created=anchor_created,
            constraint_uid=constraint.uid,
            requires_block_recorded=constraint.requires_block == slug,
        )
```

This needs `EnforceableKindStore.remove(slug)`; add to `kind_store.py`:

```python
    def remove(self, slug: str) -> None:
        """Compensation for a promotion whose rule could not be observed. Not a
        general delete: a kind rules already name is never removed this way."""
        with self._lock:
            self._conn.execute("DELETE FROM enforceable_kinds WHERE slug = ?", (slug,))
            self._conn.commit()
```

and a test in `test_kind_store.py`:

```python
def test_remove_compensates_a_failed_promotion(tmp_path):
    store = EnforceableKindStore(str(tmp_path / "m.db"))
    store.add(_kind("planning"))
    store.remove("planning")
    assert store.slugs() == []
```

Thread kinds into the existing paths: in `observe`, `ingest(observation, self._judge, self._observations, kinds=self._kinds.slugs())`; in `reproject`, pass `kinds=self._kinds.slugs()`; in `split_constraint`, pass `kinds=self._kinds.slugs()`.

- [ ] **Step 4: Register the tool**

In `src/memory/mcp_server.py` `register_tools`, after `memory_split_constraint`:

```python
    @mcp.tool(name="memory_promote_kind")
    async def memory_promote_kind(slug: str, anchor_name: str, rule_text: str) -> dict:
        """Register a kind of block that rules may require to be on the day.

        Ask the user first: this is a promotion, the one irreversible write in
        this design, and it is never made from an observation. `slug` is the
        short lowercase word tmbx will write on every block of this kind
        ("planning", "sleep"); `anchor_name` names what it is about, resolved
        against the anchors already known; `rule_text` is the rule stated in
        the user's words, filed as a durable observation so the requirement
        exists as a rule with provenance. Refuses a slug already registered.
        Samples: anchor resolution plus the six ingest judgements.
        """
        outcome = await service.promote_kind(
            slug,
            anchor_name=anchor_name,
            rule_text=rule_text,
            observed_at=datetime.now(timezone.utc),
        )
        return outcome.model_dump(mode="json")
```

Add to `INSTRUCTIONS` a paragraph:

```
memory_promote_kind registers a kind of block a rule may require to be present
(the planning session, sleep). Ask the user before calling it: it is the one
write here that mints an identity, and observe never does it.
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src uv run pytest tests/memory -q -p no:cacheprovider -m "not slow"`
Expected: all PASS (whole memory suite, offline).

- [ ] **Step 6: Commit**

```bash
git add src/memory/service.py src/memory/mcp_server.py src/memory/kind_store.py tests/memory/test_requires_block.py tests/memory/test_mcp_server.py tests/memory/test_kind_store.py
git commit -m "feat(memory): promote_kind — the one write that mints an enforceable kind, and its MCP tool (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Evals — the sixth question and `day_types`, eight draws per case

**Files:**
- Create: `tests/memory/test_eval_requires_block.py`, `tests/memory/test_eval_day_types.py`

**Interfaces:**
- Consumes: `OpenRouterJudge(api_key, base_url)` as an async context manager; `judge.requires_block(obs, kinds)`, `judge.tier(obs)`.

- [ ] **Step 1: Write the requires-block eval**

Create `tests/memory/test_eval_requires_block.py`:

```python
# tests/memory/test_eval_requires_block.py
"""Does the sixth question separate 'a block must exist' from rules about
blocks (#212, spec §1 evals)?

Eight draws per case; the rate is the assertion. A prompt validated by one
green call has not been validated (CLAUDE.md). The ambiguous case is recorded,
not asserted: its rate in the docstring is what a later prompt change compares
against.

Measured on google/gemini-3.6-flash at 8 draws when this was written:
  <fill in after the first run: positives x/8, y/8; negatives ...; ambiguous z/8>
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from memory.models import Channel, Observation, Provenance
from memory.openrouter_judge import OpenRouterJudge

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"),
]

T0 = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
SAMPLES = 8
THRESHOLD = 7
KINDS = ["planning", "sleep"]


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _obs(text: str) -> Observation:
    return Observation(text=text, channel=Channel.PLANNING, provenance=Provenance.OBSERVED,
                       session_id="eval", observed_at=T0)


async def _rate(text: str, slug: str | None) -> tuple[int, list[str]]:
    """How many of SAMPLES draws answered `slug`, and why."""
    async with _judge() as judge:
        results = await asyncio.gather(*(judge.requires_block(_obs(text), KINDS) for _ in range(SAMPLES)))
    return sum(r.slug == slug for r in results), [f"{r.slug}: {r.rationale}" for r in results]


@pytest.mark.parametrize("text", [
    "Every working day has a planning session in which the next day is timeboxed.",
    "End-of-day closure block: reserve 15-20 minutes at the end of the workday to update artifact links and board status.",
])
async def test_a_rule_that_requires_a_planning_block_names_the_kind(text):
    hits, rationales = await _rate(text, "planning")
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} chose planning for {text!r}; {rationales}"


@pytest.mark.parametrize("text", [
    "Deep work blocks run 90-120 minutes.",
    "Oats must be consumed exactly 2 hours before the gym session.",
    "Avoid back-to-back blocks of the same type; alternate deep and shallow work.",
    "No meetings before 13:00.",
])
async def test_a_rule_about_blocks_requires_none(text):
    hits, rationales = await _rate(text, None)
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} answered null for {text!r}; {rationales}"


async def test_the_ambiguous_timeboxing_sentence_is_recorded_not_asserted():
    """'I timebox my day by allocating fixed blocks' says he timeboxes; it does
    not say a session must be on the plan. Whatever the model answers is data:
    print it so the run's log carries the rate, and assert only that the
    answer is one of the offered kinds or null (the transport's own check)."""
    hits, rationales = await _rate("I timebox my day by allocating fixed blocks for tasks and activities.", "planning")
    print(f"ambiguous: {hits}/{SAMPLES} chose planning; {rationales}")
```

- [ ] **Step 2: Write the day-types eval**

Create `tests/memory/test_eval_day_types.py`:

```python
# tests/memory/test_eval_day_types.py
"""Does the tier question scope a rule to kinds of day when the statement does,
and leave it unscoped when it does not (#212, spec §1)?

Eight draws per case; the rate is the assertion. Measured on
google/gemini-3.6-flash at 8 draws when written: <fill in after the first run>.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

from memory.models import Channel, Observation, Provenance
from memory.openrouter_judge import OpenRouterJudge

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"),
]

T0 = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
SAMPLES = 8
THRESHOLD = 7


def _judge() -> OpenRouterJudge:
    return OpenRouterJudge(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )


def _obs(text: str) -> Observation:
    return Observation(text=text, channel=Channel.PLANNING, provenance=Provenance.OBSERVED,
                       session_id="eval", observed_at=T0)


async def _rate(text: str, expected: list[str]) -> tuple[int, list[list[str]]]:
    async with _judge() as judge:
        results = await asyncio.gather(*(judge.tier(_obs(text)) for _ in range(SAMPLES)))
    return sum(sorted(r.day_types) == sorted(expected) for r in results), [r.day_types for r in results]


@pytest.mark.parametrize("text,expected", [
    ("Every working day has a planning session in which the next day is timeboxed.", ["working"]),
    ("When I'm on holiday I sleep in until 09:00.", ["vacation"]),
    ("Sleep at 23:00 and wake at 07:00.", []),
])
async def test_day_types_follow_the_statements_scoping(text, expected):
    hits, answers = await _rate(text, expected)
    assert hits >= THRESHOLD, f"{hits}/{SAMPLES} answered {expected} for {text!r}; got {answers}"
```

- [ ] **Step 3: Run both evals once, record the rates**

Run: `set -a; source .env; set +a; PYTHONPATH=src uv run pytest tests/memory/test_eval_requires_block.py tests/memory/test_eval_day_types.py -q -p no:cacheprovider -m slow -s 2>&1 | tail -20`
Expected: positives and negatives ≥ 7/8; the ambiguous line printed. If a case fails, **do not loosen the threshold**: read the rationales, adjust the prompt's discriminator once, rerun. Write the measured rates into both files' module docstrings.

- [ ] **Step 4: Break one on purpose**

Temporarily change `THRESHOLD = 7` to `THRESHOLD = 9` in `test_eval_requires_block.py`, rerun one positive case, confirm it fails, restore. (A test that passes the first time has not yet earned trust.)

- [ ] **Step 5: Commit**

```bash
git add tests/memory/test_eval_requires_block.py tests/memory/test_eval_day_types.py src/memory/prompts.py
git commit -m "test(memory): evals for the required-kind question and day_types scoping, eight draws per case (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Promote `planning` on Hugo's real store, and reproject

**Files:**
- Modify: `docs/superpowers/specs/2026-09-04-required-blocks-design.md` (record what the store holds afterwards)
- Touches: `data/memory.db` (real corpus; **backup first**, never committed)

**Interfaces:**
- Consumes: `MemoryService.promote_kind`, `MemoryService.reproject`, `openrouter_judge_from_env`.
- Produces: the registry holds `planning`; the promoted rule carries `requires_block="planning"` with `day_types=["working"]`; reprojection has been previewed and applied only if the change set is exactly what was expected.

- [ ] **Step 1: Back up the store**

Run: `cp data/memory.db data/memory.db.bak-$(date +%Y%m%d)-pre-promote && ls -la data/memory.db*`
Expected: the backup exists. (Confirm with Hugo that no live session is running: `demo.py status` shows the memory service, and any open session in #plan-sessions would be mid-write.)

- [ ] **Step 2: Promote by hand**

Write and run (do not commit) a one-off script:

```bash
set -a; source .env; set +a
PYTHONPATH=src uv run python - <<'EOF'
import asyncio
from datetime import datetime, timezone
from memory.openrouter_judge import openrouter_judge_from_env
from memory.service import MemoryService

async def main():
    service = MemoryService("data/memory.db", openrouter_judge_from_env())
    outcome = await service.promote_kind(
        "planning",
        anchor_name="planning session",
        rule_text="Every working day has a planning session in which the next day is timeboxed.",
        observed_at=datetime.now(timezone.utc),
    )
    print(outcome.model_dump())
    print("kinds:", service.kinds())

asyncio.run(main())
EOF
```

Expected: `requires_block_recorded: True`, `kinds: ['planning']`. If `requires_block_recorded` is `False`, the live model did not pick the slug for the promotion's own rule: stop, rerun the eval, fix the prompt, and run `memory_reproject` on that constraint uid after the fix rather than promoting twice.

- [ ] **Step 3: Verify through the read path**

Run:

```bash
PYTHONPATH=src uv run python - <<'EOF'
from datetime import date
from memory.judge import StubJudge
from memory.service import MemoryService
s = MemoryService("data/memory.db", StubJudge())
rows = [v for v in s.get_active_constraints(date(2026, 9, 7), day_type="working") if v.requires_block]
print([(v.name, v.requires_block) for v in rows])
EOF
```

Expected: `[('Planning session', 'planning')]` (label as the model wrote it). Then the same call with `day_type="weekend"` must not list it.

- [ ] **Step 4: Preview reprojection on the whole store**

Run:

```bash
set -a; source .env; set +a
PYTHONPATH=src uv run python - <<'EOF'
import asyncio
from memory.openrouter_judge import openrouter_judge_from_env
from memory.service import MemoryService

async def main():
    s = MemoryService("data/memory.db", openrouter_judge_from_env())
    report = await s.reproject(apply=False)
    for c in report.changed:
        print(c.uid[:8], c.name, c.fields)
    print("examined", report.examined, "changed", len(report.changed), "contested", len(report.contested))

asyncio.run(main())
EOF
```

Expected: the end-of-day closure rule (`3d4605f8…`) gains `requires_block`; rules that were hand-seeded with `day_types` keep them; any other `requires_block` change is a finding to read before applying. Apply (`apply=True`) only if the changed set is the expected one. Record the outcome in the spec under "Increments".

- [ ] **Step 5: Commit the spec note**

```bash
git add docs/superpowers/specs/2026-09-04-required-blocks-design.md
git commit -m "docs(specs): planning promoted on the live store; reprojection outcome recorded (#212)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Whole-suite check, push, PR

- [ ] **Step 1: Full offline suite from the worktree root**

Run: `uv run pytest -q -p no:cacheprovider -m "not slow" 2>&1 | tail -3`
Expected: all pass (the memory suite plus everything else; `tests/memory` runs with `PYTHONPATH=src` via its conftest — if not, prefix the command).

- [ ] **Step 2: Rebase onto origin/main if it moved, rerun, push, open the PR**

```bash
git fetch origin && git rebase origin/main
uv run pytest -q -p no:cacheprovider -m "not slow" 2>&1 | tail -2
git push -u origin feat/required-blocks-memory
gh pr create --base main --title "memory: rules can require a registered kind of block (#212, increment 1 of required blocks)" --body "..."
```

PR body: what lands (registry, promotion, sixth judgement, day_types writer, schema v4, reprojection), the spike's answer, the eval rates, the live-store promotion result, and the human checklist (Hugo confirms the `planning` rule shows on a working day and not on a weekend via `memory_get_active_constraints`). End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Self-review

**Spec coverage (§1 of the spec):** registry table + promotion (Tasks 1, 3, 8); `requires_block` field, sixth judgement, verified closed choice, ingest gather, reprojection, view exposure (Tasks 2, 4, 6, 7); `day_types` writer (Tasks 5, 7); read path unchanged (Task 2 step 5 runs the AST guard); evals with the ambiguous case recorded (Task 9); `frame_slot` marked dead (Task 2); first promotion by hand (Task 10); spike first (Task 0). §2–§4 of the spec are increments 2 and 3 and belong to later plans.

**Placeholders:** the two eval docstrings contain `<fill in after the first run>` by design — they are measurement slots filled in Task 9 step 3, not unfinished plan text. The spike result line in Task 0 step 5 is the same.

**Type consistency:** `ingest(..., kinds: list[str] = ())`, `_ingest(..., kinds)`, `_judge_all(observations, judge, kinds)`, `reproject(..., kinds=)`, `split(..., kinds=)`, `MemoryService.kinds() -> list[str]`, `EnforceableKindStore.slugs() -> list[str]`, `RequiresBlockJudgement.slug`, `IngestResult.requires_block`, `Constraint.requires_block`, `ConstraintView.requires_block`, `StubJudge(requires_blocks=, day_types=)`, `EnforceableKindStore.remove(slug)` — used with the same names and types throughout.
