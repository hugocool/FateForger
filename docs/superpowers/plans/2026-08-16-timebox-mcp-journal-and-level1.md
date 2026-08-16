# Timebox Journal Emitter + Level 1 Op Vocabulary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a durable patch journal into the existing timeboxing agent (#123), then build the level-1 timebox MCP server against it (#146).

**Architecture:** Part A instruments the *existing* `TimeboxPatcher` and `CalendarSubmitter` by decoration — both are constructed at exactly one site each (`agent.py:445-446`), so instrumentation touches two lines of an 8,563-line file. The journal itself lives in the *new* package `src/tmbx/journal/`, so it is designed once and shared: Part A writes to it from `fateforger`, Part B reads and writes it from the server. Part B builds a clean-room core — models, ops, rendering, calendar port — with zero imports from `fateforger.*`.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLModel + aiosqlite (journal), `mcp` ≥1.26 (FastMCP), pytest with `asyncio_mode = "auto"`, Poetry.

**Spec:** `docs/superpowers/specs/2026-08-16-timebox-mcp-design.md`
**Tickets:** #123 (Part A), #146 (Part B). Map: #121. Vocabulary resolution: #122.

## Global Constraints

- **`src/tmbx/` must never import from `fateforger.*`.** The reverse (`fateforger` importing `tmbx`) is allowed and is how Part A works. Enforced by a test in Task 1.
- **Run tests with** `poetry run pytest <path> -v`. Config is in `pyproject.toml` — `asyncio_mode = "auto"`, so async tests need no decorator.
- **No test may require a live Google Calendar, a live LLM, or network access.**
- **Commit per task, as each task's commit step specifies.** The repo's default (`AGENTS.md`) forbids unprompted commits; the user granted commit authority for this execution run, scoped to the branch `feat/tmbx-journal-level1`. **Never push, and never touch `main`.**
- **Journal database:** its own SQLite file at `data/tmbx_journal.db`, separate from `data/admonish.db`. `AGENTS.md` forbids runtime `ensure_*` table creation in live paths; this plan complies by exposing an explicit `init_journal()` called from a CLI entrypoint and from server startup — never lazily from a write path.
- **Every journal row carries `schema_version`.** Current value: `1`.
- **Type-annotate everything.** The repo uses Pydantic v2 and mypy; `from __future__ import annotations` at the top of every new module.

---

# Part A — Journal emitter against existing code (#123)

This part ships first and deliberately lands in code slated for deletion. Its value is time-dependent: every session that runs without it is corpus neither map recovers.

---

### Task 1: Package skeleton and the import boundary

**Files:**
- Create: `src/tmbx/__init__.py`
- Create: `src/tmbx/journal/__init__.py`
- Test: `tests/unit/tmbx/test_import_boundary.py`

**Interfaces:**
- Consumes: nothing
- Produces: the `tmbx` package root. Every later task adds modules under it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_import_boundary.py
"""tmbx must never import from fateforger. The reverse is allowed."""
from __future__ import annotations

import ast
from pathlib import Path

TMBX_ROOT = Path(__file__).resolve().parents[3] / "src" / "tmbx"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_tmbx_never_imports_fateforger():
    offenders: list[str] = []
    for py in TMBX_ROOT.rglob("*.py"):
        for mod in _imported_modules(py):
            if mod == "fateforger" or mod.startswith("fateforger."):
                offenders.append(f"{py.relative_to(TMBX_ROOT)} imports {mod}")
    assert offenders == [], "tmbx must not import fateforger:\n" + "\n".join(offenders)


def test_tmbx_package_exists():
    import tmbx

    assert tmbx.__name__ == "tmbx"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_import_boundary.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx'`

- [ ] **Step 3: Create the package**

```python
# src/tmbx/__init__.py
"""Timebox core and MCP server.

This package is clean-room: it must never import from ``fateforger``.
The reverse direction is intentional and is how the legacy agent writes
to the shared journal.
"""

from __future__ import annotations

__all__: list[str] = []
```

```python
# src/tmbx/journal/__init__.py
"""Durable patch journal, shared by the legacy agent and the MCP server."""

from __future__ import annotations

__all__: list[str] = []
```

Create `tests/unit/tmbx/__init__.py` as an empty file so pytest collects the directory.

- [ ] **Step 4: Register the package for imports**

In `pyproject.toml`, find the `[tool.poetry]` section's `packages` list and add the `tmbx` entry alongside the existing `fateforger` one. If the list reads `packages = [{include = "fateforger", from = "src"}]`, it becomes:

```toml
packages = [
    {include = "fateforger", from = "src"},
    {include = "tmbx", from = "src"},
]
```

Then run `poetry install` so the new package is importable.

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_import_boundary.py -v`
Expected: PASS, 2 tests

- [ ] **Step 6: Commit**

```bash
git add src/tmbx tests/unit/tmbx pyproject.toml
git commit -m "feat(tmbx): package skeleton with enforced fateforger import boundary"
```

---

### Task 2: Journal schema and store

**Files:**
- Create: `src/tmbx/journal/models.py`
- Create: `src/tmbx/journal/store.py`
- Test: `tests/unit/tmbx/test_journal_store.py`

**Interfaces:**
- Consumes: the `tmbx` package from Task 1
- Produces:
  - `ConstraintRef(uid: str, uid_kind: Literal["minted","derived"], reason: str | None)`
  - `JournalEntry` — SQLModel table `tmbx_journal`, columns listed in Step 3
  - `JournalStore(sessionmaker)` with `async append(entry) -> int`, `async by_day(calendar_id, date) -> list[JournalEntry]`, `async get(entry_id) -> JournalEntry | None`
  - `init_journal(db_path) -> async_sessionmaker[AsyncSession]`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_journal_store.py
from __future__ import annotations

from datetime import date

import pytest

from tmbx.journal.models import ConstraintRef, JournalEntry, PatchOutcome
from tmbx.journal.store import JournalStore, init_journal


@pytest.fixture
async def store(tmp_path):
    sessionmaker = await init_journal(tmp_path / "j.db")
    return JournalStore(sessionmaker)


async def test_append_returns_id_and_roundtrips(store):
    entry = JournalEntry(
        calendar_id="primary",
        plan_date=date(2026, 8, 17),
        instruction="move lunch to 13:00",
        ops_json='{"ops":[]}',
        ops_schema_version=1,
        outcome=PatchOutcome.APPLIED,
    )
    entry.set_constraints([ConstraintRef(uid="c1", uid_kind="minted", reason="graphflow_turn")])

    entry_id = await store.append(entry)
    assert entry_id > 0

    loaded = await store.get(entry_id)
    assert loaded is not None
    assert loaded.instruction == "move lunch to 13:00"
    assert loaded.outcome == PatchOutcome.APPLIED
    refs = loaded.get_constraints()
    assert refs == [ConstraintRef(uid="c1", uid_kind="minted", reason="graphflow_turn")]


async def test_by_day_filters_and_orders(store):
    for day, instr in [
        (date(2026, 8, 17), "first"),
        (date(2026, 8, 18), "other day"),
        (date(2026, 8, 17), "second"),
    ]:
        await store.append(
            JournalEntry(
                calendar_id="primary",
                plan_date=day,
                instruction=instr,
                ops_json="{}",
                ops_schema_version=1,
                outcome=PatchOutcome.APPLIED,
            )
        )

    rows = await store.by_day("primary", date(2026, 8, 17))
    assert [r.instruction for r in rows] == ["first", "second"]


async def test_by_day_scopes_to_calendar(store):
    await store.append(
        JournalEntry(
            calendar_id="work",
            plan_date=date(2026, 8, 17),
            instruction="work cal",
            ops_json="{}",
            ops_schema_version=1,
            outcome=PatchOutcome.APPLIED,
        )
    )
    assert await store.by_day("primary", date(2026, 8, 17)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_journal_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.journal.models'`

- [ ] **Step 3: Write the models**

```python
# src/tmbx/journal/models.py
"""Journal row schema.

One row per patch attempt. Disposition is NOT stored — it is derived from
the rows (see ``tmbx.journal.disposition``), because hosts forget to report
and the journal cannot.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Field, SQLModel

JOURNAL_SCHEMA_VERSION = 1


class PatchOutcome(str, Enum):
    """Did the patch itself validate and apply?"""

    APPLIED = "applied"
    PARSE_FAILED = "parse_failed"
    APPLY_FAILED = "apply_failed"
    VALIDATION_FAILED = "validation_failed"


class EntryKind(str, Enum):
    """What produced this row."""

    ATTEMPT = "attempt"
    COMMIT = "commit"
    UNDO = "undo"


class ConstraintRef(BaseModel):
    """A constraint that was in context when the patch was produced.

    ``uid_kind`` records whether the uid was a real minted identifier or a
    content-derived fallback. Derived keys change when the constraint text is
    edited, so downstream consumers must be able to tell them apart.
    """

    uid: str
    uid_kind: Literal["minted", "derived"]
    reason: str | None = None


class JournalEntry(SQLModel, table=True):
    """One patch attempt, commit, or undo."""

    __tablename__ = "tmbx_journal"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )
    kind: EntryKind = Field(default=EntryKind.ATTEMPT, index=True)

    calendar_id: str = Field(index=True)
    plan_date: date_type = Field(index=True)

    instruction: str | None = None
    constraints_json: str = Field(default="[]")

    ops_json: str = Field(default="{}")
    ops_schema_version: int = Field(default=JOURNAL_SCHEMA_VERSION)

    outcome: PatchOutcome = Field(default=PatchOutcome.APPLIED)
    error: str | None = None

    tx_id: str | None = Field(default=None, index=True)
    undoes_tx: str | None = Field(default=None, index=True)

    # Undo state. Populated on COMMIT rows only, so undo survives a restart —
    # holding it in a process-local dict is the defect behind #112.
    before_json: str | None = Field(
        default=None, description="Calendar events as they were before this commit"
    )
    post_etags_json: str | None = Field(
        default=None,
        description="Etags immediately after this commit wrote. Undo compares "
        "live state against these to refuse clobbering a newer edit.",
    )

    def set_constraints(self, refs: list[ConstraintRef]) -> None:
        """Serialise constraint refs into the JSON column."""
        self.constraints_json = json.dumps([r.model_dump() for r in refs])

    def get_constraints(self) -> list[ConstraintRef]:
        """Deserialise constraint refs from the JSON column."""
        raw = json.loads(self.constraints_json or "[]")
        return [ConstraintRef.model_validate(item) for item in raw]


__all__ = [
    "JOURNAL_SCHEMA_VERSION",
    "ConstraintRef",
    "EntryKind",
    "JournalEntry",
    "PatchOutcome",
]
```

- [ ] **Step 4: Write the store**

```python
# src/tmbx/journal/store.py
"""Async SQLite store for journal rows."""

from __future__ import annotations

from datetime import date as date_type
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from .models import JournalEntry

DEFAULT_JOURNAL_PATH = Path("data/tmbx_journal.db")


def journal_sessionmaker(
    db_path: Path | str = DEFAULT_JOURNAL_PATH,
) -> async_sessionmaker[AsyncSession]:
    """Build a sessionmaker without creating the schema or touching the loop.

    ``create_async_engine`` connects lazily, so this is safe to call from a
    synchronous constructor even while an event loop is running — which is
    exactly the situation in the Slack bot. Never call ``asyncio.run`` or
    ``run_until_complete`` from there; both raise inside a running loop, and a
    swallowed exception would leave the journal silently disabled.

    The schema must already exist. Create it with ``init_journal``.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_journal(
    db_path: Path | str = DEFAULT_JOURNAL_PATH,
) -> async_sessionmaker[AsyncSession]:
    """Create the journal schema and return a sessionmaker.

    Explicit entrypoint — call it from an async server startup or the
    ``tmbx-init-journal`` command, never from a write path. Repo policy
    forbids runtime table creation in live paths.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[JournalEntry.__table__])
    return async_sessionmaker(engine, expire_on_commit=False)


def init_journal_cli() -> None:
    """``tmbx-init-journal`` entrypoint — create the schema, then exit."""
    import asyncio

    asyncio.run(init_journal())
    print(f"journal ready at {DEFAULT_JOURNAL_PATH}")


class JournalStore:
    """Append-only reader/writer over ``tmbx_journal``."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def append(self, entry: JournalEntry) -> int:
        """Persist one row and return its id."""
        async with self._sessionmaker() as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            assert entry.id is not None
            return entry.id

    async def get(self, entry_id: int) -> JournalEntry | None:
        """Fetch one row by id."""
        async with self._sessionmaker() as session:
            return await session.get(JournalEntry, entry_id)

    async def by_day(
        self, calendar_id: str, plan_date: date_type
    ) -> list[JournalEntry]:
        """All rows for one calendar-day, oldest first."""
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(JournalEntry)
                .where(JournalEntry.calendar_id == calendar_id)
                .where(JournalEntry.plan_date == plan_date)
                .order_by(JournalEntry.id)
            )
            return list(result.scalars().all())

    async def by_tx_id(self, tx_id: str) -> JournalEntry | None:
        """Fetch a commit row by its transaction id.

        Undo needs this: the pre-commit calendar state and the post-commit
        etags live on the row, so undo works after a restart.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(JournalEntry).where(JournalEntry.tx_id == tx_id)
            )
            return result.scalars().first()


__all__ = [
    "DEFAULT_JOURNAL_PATH",
    "JournalStore",
    "init_journal",
    "init_journal_cli",
    "journal_sessionmaker",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_journal_store.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Commit**

```bash
git add src/tmbx/journal tests/unit/tmbx/test_journal_store.py
git commit -m "feat(tmbx): journal schema and async SQLite store"
```

---

### Task 3: Disposition derivation

**Files:**
- Create: `src/tmbx/journal/disposition.py`
- Test: `tests/unit/tmbx/test_disposition.py`

**Interfaces:**
- Consumes: `JournalEntry`, `EntryKind`, `PatchOutcome` from Task 2
- Produces: `Disposition` enum and `derive_dispositions(entries: list[JournalEntry]) -> dict[int, Disposition]`

Disposition is derived rather than reported: `undone` when a later undo row references the commit's `tx_id`; `superseded` when another commit for the same day landed after it; `abandoned` when an attempt applied cleanly but never became a commit; `accepted` when a commit stands.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_disposition.py
from __future__ import annotations

from datetime import date

from tmbx.journal.disposition import Disposition, derive_dispositions
from tmbx.journal.models import EntryKind, JournalEntry, PatchOutcome

DAY = date(2026, 8, 17)


def _entry(eid, kind, *, tx_id=None, undoes_tx=None, outcome=PatchOutcome.APPLIED):
    return JournalEntry(
        id=eid,
        kind=kind,
        calendar_id="primary",
        plan_date=DAY,
        ops_json="{}",
        ops_schema_version=1,
        outcome=outcome,
        tx_id=tx_id,
        undoes_tx=undoes_tx,
    )


def test_lone_commit_is_accepted():
    entries = [_entry(1, EntryKind.COMMIT, tx_id="tx1")]
    assert derive_dispositions(entries) == {1: Disposition.ACCEPTED}


def test_commit_followed_by_undo_is_undone():
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.UNDO, tx_id="tx2", undoes_tx="tx1"),
    ]
    result = derive_dispositions(entries)
    assert result[1] == Disposition.UNDONE
    assert result[2] == Disposition.ACCEPTED


def test_earlier_commit_is_superseded():
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.COMMIT, tx_id="tx2"),
    ]
    result = derive_dispositions(entries)
    assert result[1] == Disposition.SUPERSEDED
    assert result[2] == Disposition.ACCEPTED


def test_undone_beats_superseded():
    """An undone commit stays undone even if a later commit exists."""
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.UNDO, tx_id="tx2", undoes_tx="tx1"),
        _entry(3, EntryKind.COMMIT, tx_id="tx3"),
    ]
    assert derive_dispositions(entries)[1] == Disposition.UNDONE


def test_applied_attempt_never_committed_is_abandoned():
    entries = [_entry(1, EntryKind.ATTEMPT)]
    assert derive_dispositions(entries) == {1: Disposition.ABANDONED}


def test_failed_attempt_is_failed_not_abandoned():
    entries = [_entry(1, EntryKind.ATTEMPT, outcome=PatchOutcome.APPLY_FAILED)]
    assert derive_dispositions(entries) == {1: Disposition.FAILED}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_disposition.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.journal.disposition'`

- [ ] **Step 3: Write the implementation**

```python
# src/tmbx/journal/disposition.py
"""Derive the disposition of each journal row from the rows themselves.

Hosts forget to report outcomes; the journal cannot. Deriving keeps the
training label honest, which matters because it feeds both the prompt
compiler and the constraint memory server.
"""

from __future__ import annotations

from enum import Enum

from .models import EntryKind, JournalEntry, PatchOutcome


class Disposition(str, Enum):
    ACCEPTED = "accepted"
    UNDONE = "undone"
    SUPERSEDED = "superseded"
    ABANDONED = "abandoned"
    FAILED = "failed"


def derive_dispositions(entries: list[JournalEntry]) -> dict[int, Disposition]:
    """Map entry id → disposition for one calendar-day's rows.

    Args:
        entries: Rows for a single ``(calendar_id, plan_date)``, any order.

    Returns:
        Dict keyed by entry id. Precedence: failed → undone → superseded →
        abandoned → accepted.
    """
    ordered = sorted(entries, key=lambda e: (e.id or 0))
    undone_tx = {e.undoes_tx for e in ordered if e.undoes_tx}

    commit_ids = [
        e.id
        for e in ordered
        if e.kind in (EntryKind.COMMIT, EntryKind.UNDO) and e.id is not None
    ]
    last_commit_id = commit_ids[-1] if commit_ids else None

    result: dict[int, Disposition] = {}
    for entry in ordered:
        if entry.id is None:
            continue

        if entry.outcome is not PatchOutcome.APPLIED:
            result[entry.id] = Disposition.FAILED
            continue

        if entry.kind is EntryKind.ATTEMPT:
            result[entry.id] = Disposition.ABANDONED
            continue

        if entry.tx_id and entry.tx_id in undone_tx:
            result[entry.id] = Disposition.UNDONE
            continue

        if last_commit_id is not None and entry.id < last_commit_id:
            result[entry.id] = Disposition.SUPERSEDED
            continue

        result[entry.id] = Disposition.ACCEPTED

    return result


__all__ = ["Disposition", "derive_dispositions"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_disposition.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add src/tmbx/journal/disposition.py tests/unit/tmbx/test_disposition.py
git commit -m "feat(tmbx): derive journal dispositions from rows, not host reports"
```

---

### Task 4: Persist constraint extraction provenance

**Files:**
- Modify: `src/fateforger/agents/timeboxing/agent.py` — inside `_queue_constraint_extraction` (definition at line 1375)
- Test: `tests/unit/test_constraint_extraction_reason.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: every constraint produced by extraction carries `hints["extraction_reason"]`, one of `"graphflow_turn"` or `"refine_background_memory"`. Task 5 reads this.

**Why this task exists:** `reason` is currently passed to `_queue_constraint_extraction` and used only for debug logging (`agent.py:1405`, `agent.py:1648`). It never reaches the constraint record. Without it the journal cannot distinguish constraints extracted from what the user actually said from constraints extracted from machine-authored repair text (`nodes.py:596`) — and the synthetic path fires precisely when preflight found plan issues, so those constraints correlate with failure. A consumer learning from unlabelled rows would get the causation backwards.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_constraint_extraction_reason.py
"""Extraction reason must be persisted onto each constraint's hints."""
from __future__ import annotations

from fateforger.agents.timeboxing.agent import _stamp_extraction_reason
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
)


def _constraint(**hints):
    return Constraint(
        name="Dinner",
        description="Dinner at 18:30",
        necessity=ConstraintNecessity.MUST,
        user_id="u1",
        hints=dict(hints),
    )


def test_stamps_reason_onto_empty_hints():
    c = _constraint()
    _stamp_extraction_reason([c], reason="graphflow_turn")
    assert c.hints["extraction_reason"] == "graphflow_turn"


def test_preserves_existing_hints():
    c = _constraint(uid="abc123")
    _stamp_extraction_reason([c], reason="refine_background_memory")
    assert c.hints["uid"] == "abc123"
    assert c.hints["extraction_reason"] == "refine_background_memory"


def test_does_not_overwrite_existing_reason():
    """First extraction wins — a later pass must not relabel provenance."""
    c = _constraint(extraction_reason="graphflow_turn")
    _stamp_extraction_reason([c], reason="refine_background_memory")
    assert c.hints["extraction_reason"] == "graphflow_turn"


def test_tolerates_empty_list():
    _stamp_extraction_reason([], reason="graphflow_turn")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_constraint_extraction_reason.py -v`
Expected: FAIL — `ImportError: cannot import name '_stamp_extraction_reason'`

- [ ] **Step 3: Add the helper**

Add this module-level function to `src/fateforger/agents/timeboxing/agent.py`, immediately above the class that defines `_queue_constraint_extraction`:

```python
def _stamp_extraction_reason(constraints: Iterable[Any], *, reason: str) -> None:
    """Record which extraction pass produced each constraint.

    Distinguishes constraints extracted from what the user actually said
    (``graphflow_turn``) from those extracted from machine-authored repair
    text (``refine_background_memory``). The second path fires when preflight
    found plan issues, so those constraints correlate with failure — consumers
    must be able to filter them out rather than learn from them.

    First write wins: a later pass never relabels provenance.
    """
    for constraint in constraints or []:
        hints = getattr(constraint, "hints", None)
        if hints is None:
            continue
        if not isinstance(hints, dict):
            continue
        if hints.get("extraction_reason"):
            continue
        hints["extraction_reason"] = reason
        # SQLModel JSON columns need reassignment to register as dirty.
        constraint.hints = dict(hints)
```

- [ ] **Step 4: Call it from the extraction path**

Inside `_queue_constraint_extraction`'s `_background()` coroutine, immediately after `interpretation` is obtained (the line `interpretation = await self._interpret_constraints(...)`, currently `agent.py:1396-1398`), add:

```python
                _stamp_extraction_reason(
                    interpretation.constraints or [], reason=reason
                )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_constraint_extraction_reason.py -v`
Expected: PASS, 4 tests

- [ ] **Step 6: Verify nothing else broke**

Run: `poetry run pytest tests/unit -k constraint -v`
Expected: PASS — no regressions in the existing constraint suite.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/agents/timeboxing/agent.py tests/unit/test_constraint_extraction_reason.py
git commit -m "feat(constraints): persist extraction reason onto constraint hints"
```

---

### Task 5: Constraint reference extraction

**Files:**
- Create: `src/tmbx/journal/constraint_refs.py`
- Test: `tests/unit/tmbx/test_constraint_refs.py`

**Interfaces:**
- Consumes: `ConstraintRef` from Task 2; the `hints["extraction_reason"]` written in Task 4
- Produces: `constraint_refs(objects: Iterable[Any]) -> list[ConstraintRef]` — duck-typed over anything with `.hints`, `.name`, `.description`, `.necessity`, `.scope`, so `tmbx` stays free of `fateforger` imports.

**Why the derived fallback exists:** `Constraint` has no uid column. `id` is an autoincrement int; the joinable uid lives in `hints["uid"]` and is optional. When absent, the legacy code derives a key from `name|description|necessity|scope` (`preferences.py:604-614`) — which changes whenever the constraint text is edited. Emitting both kinds, tagged, makes that degradation measurable rather than silent.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_constraint_refs.py
from __future__ import annotations

from types import SimpleNamespace

from tmbx.journal.constraint_refs import constraint_refs, derived_uid


def _c(hints=None, name="Dinner", description="at 18:30", necessity="must", scope="profile"):
    return SimpleNamespace(
        hints=hints if hints is not None else {},
        name=name,
        description=description,
        necessity=necessity,
        scope=scope,
    )


def test_minted_uid_is_used_and_tagged():
    refs = constraint_refs([_c(hints={"uid": "abc123"})])
    assert refs[0].uid == "abc123"
    assert refs[0].uid_kind == "minted"


def test_missing_uid_falls_back_to_derived_and_is_tagged():
    refs = constraint_refs([_c()])
    assert refs[0].uid_kind == "derived"
    assert refs[0].uid == derived_uid(_c())


def test_reason_is_carried_through():
    refs = constraint_refs([_c(hints={"uid": "x", "extraction_reason": "graphflow_turn"})])
    assert refs[0].reason == "graphflow_turn"


def test_missing_reason_is_none_not_guessed():
    refs = constraint_refs([_c(hints={"uid": "x"})])
    assert refs[0].reason is None


def test_derived_uid_changes_when_text_is_edited():
    """This is the defect being measured, asserted so it cannot regress silently."""
    before = derived_uid(_c(description="at 18:30"))
    after = derived_uid(_c(description="at 19:00"))
    assert before != after


def test_enum_valued_fields_are_normalised():
    necessity = SimpleNamespace(value="must")
    refs = constraint_refs([_c(necessity=necessity)])
    assert refs[0].uid_kind == "derived"


def test_empty_input():
    assert constraint_refs([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_constraint_refs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.journal.constraint_refs'`

- [ ] **Step 3: Write the implementation**

```python
# src/tmbx/journal/constraint_refs.py
"""Extract journal-ready constraint references.

Duck-typed on purpose: this module must not import ``fateforger``.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from .models import ConstraintRef


def _plain(value: Any) -> str:
    """Normalise enums, None, and scalars to a plain string."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def derived_uid(obj: Any) -> str:
    """Content-derived fallback key, mirroring the legacy signature.

    Unstable by construction: editing the constraint's text changes the key.
    Callers must tag results produced this way so the instability is visible.
    """
    signature = "|".join(
        [
            _plain(getattr(obj, "name", "")),
            _plain(getattr(obj, "description", "")),
            _plain(getattr(obj, "necessity", "")),
            _plain(getattr(obj, "scope", "")),
        ]
    )
    return "d:" + hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]


def constraint_refs(objects: Iterable[Any]) -> list[ConstraintRef]:
    """Build ``ConstraintRef`` rows from constraint-like objects."""
    refs: list[ConstraintRef] = []
    for obj in objects or []:
        hints = getattr(obj, "hints", None)
        hints = hints if isinstance(hints, dict) else {}

        minted = str(hints.get("uid") or "").strip()
        if minted:
            uid, kind = minted, "minted"
        else:
            uid, kind = derived_uid(obj), "derived"

        reason = hints.get("extraction_reason")
        refs.append(
            ConstraintRef(
                uid=uid,
                uid_kind=kind,  # type: ignore[arg-type]
                reason=str(reason) if reason else None,
            )
        )
    return refs


__all__ = ["constraint_refs", "derived_uid"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_constraint_refs.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add src/tmbx/journal/constraint_refs.py tests/unit/tmbx/test_constraint_refs.py
git commit -m "feat(tmbx): constraint refs with minted-vs-derived uid tagging"
```

---

### Task 6: Journaling decorators

**Files:**
- Create: `src/tmbx/journal/instrument.py`
- Test: `tests/unit/tmbx/test_instrument.py`

**Interfaces:**
- Consumes: `JournalStore` (Task 2), `constraint_refs` (Task 5), `JournalEntry`/`EntryKind`/`PatchOutcome` (Task 2)
- Produces:
  - `JournalingPatcher(inner, store, calendar_id_fn)` — exposes `apply_patch(**kwargs)` matching the wrapped signature
  - `JournalingSubmitter(inner, store)` — exposes `submit_plan(...)`, `undo_transaction(tx)`, `undo_last()`, and passes through `last_transaction`

**Design note:** decoration rather than call-site edits. `TimeboxPatcher` and `CalendarSubmitter` are each constructed exactly once, on adjacent lines (`agent.py:445-446`), so this instruments five call sites by changing two lines. Journal failures must never break planning — every write is wrapped in a broad `except` that logs and continues.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_instrument.py
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from tmbx.journal.instrument import JournalingPatcher, JournalingSubmitter
from tmbx.journal.models import EntryKind, PatchOutcome
from tmbx.journal.store import JournalStore, init_journal

DAY = date(2026, 8, 17)


@pytest.fixture
async def store(tmp_path):
    return JournalStore(await init_journal(tmp_path / "j.db"))


class _FakePatcher:
    def __init__(self, *, raises: Exception | None = None):
        self.raises = raises

    async def apply_patch(self, **kwargs):
        if self.raises:
            raise self.raises
        plan = SimpleNamespace(date=DAY)
        patch = SimpleNamespace(model_dump_json=lambda: '{"ops":[{"op":"ue"}]}')
        return plan, patch


class _FakeSubmitter:
    def __init__(self):
        self.last_transaction = None

    async def submit_plan(self, desired, **kwargs):
        return SimpleNamespace(status="committed", ops=[], results=[])

    async def undo_transaction(self, tx):
        return SimpleNamespace(status="committed", ops=[], results=[])


async def test_successful_patch_writes_attempt_row(store):
    patcher = JournalingPatcher(_FakePatcher(), store, calendar_id="primary")
    await patcher.apply_patch(
        stage="Refine",
        current=SimpleNamespace(date=DAY),
        user_message="move lunch",
        constraints=[SimpleNamespace(hints={"uid": "c1", "extraction_reason": "graphflow_turn"})],
    )

    rows = await store.by_day("primary", DAY)
    assert len(rows) == 1
    assert rows[0].kind is EntryKind.ATTEMPT
    assert rows[0].outcome is PatchOutcome.APPLIED
    assert rows[0].instruction == "move lunch"
    assert rows[0].get_constraints()[0].uid == "c1"
    assert rows[0].get_constraints()[0].reason == "graphflow_turn"


async def test_failed_patch_writes_failure_row_and_reraises(store):
    patcher = JournalingPatcher(
        _FakePatcher(raises=ValueError("bad patch")), store, calendar_id="primary"
    )
    with pytest.raises(ValueError):
        await patcher.apply_patch(
            stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
        )

    rows = await store.by_day("primary", DAY)
    assert rows[0].outcome is PatchOutcome.APPLY_FAILED
    assert "bad patch" in (rows[0].error or "")


async def test_journal_failure_never_breaks_planning(store):
    class _BrokenStore:
        async def append(self, entry):
            raise RuntimeError("disk full")

    patcher = JournalingPatcher(_FakePatcher(), _BrokenStore(), calendar_id="primary")
    plan, patch = await patcher.apply_patch(
        stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
    )
    assert plan is not None


async def test_submit_writes_commit_row_with_tx_id(store):
    sub = JournalingSubmitter(_FakeSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")

    rows = await store.by_day("primary", DAY)
    assert rows[0].kind is EntryKind.COMMIT
    assert rows[0].tx_id is not None
    assert getattr(tx, "tmbx_tx_id", None) == rows[0].tx_id


async def test_undo_row_references_the_commit(store):
    sub = JournalingSubmitter(_FakeSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")
    await sub.undo_transaction(tx)

    rows = await store.by_day("primary", DAY)
    assert rows[1].kind is EntryKind.UNDO
    assert rows[1].undoes_tx == rows[0].tx_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_instrument.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.journal.instrument'`

- [ ] **Step 3: Write the implementation**

```python
# src/tmbx/journal/instrument.py
"""Decorators that journal the legacy patcher and submitter.

Instrumentation by decoration: both wrapped objects are constructed at a
single site each, so five call sites get covered by two changed lines.

Journal writes never break planning. Every write is guarded.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as date_type
from typing import Any, Iterable

from .constraint_refs import constraint_refs
from .models import EntryKind, JournalEntry, PatchOutcome

logger = logging.getLogger(__name__)


def _plan_date(obj: Any) -> date_type:
    """Best-effort plan date, falling back to today."""
    value = getattr(obj, "date", None)
    return value if isinstance(value, date_type) else date_type.today()


def _ops_json(patch: Any) -> str:
    """Serialise a patch to JSON without assuming its type."""
    dumper = getattr(patch, "model_dump_json", None)
    if callable(dumper):
        try:
            return str(dumper())
        except Exception:  # pragma: no cover - defensive
            pass
    return "{}"


class JournalingPatcher:
    """Wrap a patcher, recording one attempt row per ``apply_patch`` call."""

    def __init__(self, inner: Any, store: Any, *, calendar_id: str = "primary") -> None:
        self._inner = inner
        self._store = store
        self._calendar_id = calendar_id

    def __getattr__(self, name: str) -> Any:
        """Pass through everything not explicitly wrapped."""
        return getattr(self._inner, name)

    async def _write(self, entry: JournalEntry) -> None:
        try:
            await self._store.append(entry)
        except Exception:
            logger.warning("journal write failed; continuing", exc_info=True)

    async def apply_patch(self, **kwargs: Any) -> Any:
        current = kwargs.get("current")
        constraints: Iterable[Any] = kwargs.get("constraints") or []
        instruction = kwargs.get("user_message")

        base = dict(
            calendar_id=self._calendar_id,
            plan_date=_plan_date(current),
            instruction=instruction,
            kind=EntryKind.ATTEMPT,
        )
        refs = constraint_refs(constraints)

        try:
            result = await self._inner.apply_patch(**kwargs)
        except Exception as exc:
            entry = JournalEntry(
                **base, outcome=PatchOutcome.APPLY_FAILED, error=str(exc)[:2000]
            )
            entry.set_constraints(refs)
            await self._write(entry)
            raise

        _, patch = result
        entry = JournalEntry(
            **base, outcome=PatchOutcome.APPLIED, ops_json=_ops_json(patch)
        )
        entry.set_constraints(refs)
        await self._write(entry)
        return result


class JournalingSubmitter:
    """Wrap a submitter, recording commit and undo rows.

    Stamps ``tmbx_tx_id`` onto each returned transaction so a later undo can
    reference the commit it reverses.
    """

    def __init__(self, inner: Any, store: Any) -> None:
        self._inner = inner
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def _write(self, entry: JournalEntry) -> None:
        try:
            await self._store.append(entry)
        except Exception:
            logger.warning("journal write failed; continuing", exc_info=True)

    async def submit_plan(self, desired: Any, **kwargs: Any) -> Any:
        tx = await self._inner.submit_plan(desired, **kwargs)
        tx_id = uuid.uuid4().hex
        try:
            setattr(tx, "tmbx_tx_id", tx_id)
        except Exception:  # pragma: no cover - defensive
            pass

        entry = JournalEntry(
            calendar_id=kwargs.get("calendar_id", "primary"),
            plan_date=_plan_date(desired),
            kind=EntryKind.COMMIT,
            outcome=PatchOutcome.APPLIED,
            tx_id=tx_id,
        )
        await self._write(entry)
        return tx

    async def undo_transaction(self, tx: Any) -> Any:
        undo_tx = await self._inner.undo_transaction(tx)
        if undo_tx is None:
            return None

        entry = JournalEntry(
            calendar_id=getattr(tx, "tmbx_calendar_id", "primary"),
            plan_date=getattr(tx, "tmbx_plan_date", date_type.today()),
            kind=EntryKind.UNDO,
            outcome=PatchOutcome.APPLIED,
            tx_id=uuid.uuid4().hex,
            undoes_tx=getattr(tx, "tmbx_tx_id", None),
        )
        await self._write(entry)
        return undo_tx

    async def undo_last(self) -> Any:
        tx = getattr(self._inner, "last_transaction", None)
        if tx is None:
            return await self._inner.undo_last()
        return await self.undo_transaction(tx)


__all__ = ["JournalingPatcher", "JournalingSubmitter"]
```

- [ ] **Step 4: Carry calendar_id and plan_date onto the transaction**

In `submit_plan`, immediately after `setattr(tx, "tmbx_tx_id", tx_id)`, add so undo rows land on the right day:

```python
        try:
            setattr(tx, "tmbx_calendar_id", kwargs.get("calendar_id", "primary"))
            setattr(tx, "tmbx_plan_date", _plan_date(desired))
        except Exception:  # pragma: no cover - defensive
            pass
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_instrument.py -v`
Expected: PASS, 5 tests

- [ ] **Step 6: Commit**

```bash
git add src/tmbx/journal/instrument.py tests/unit/tmbx/test_instrument.py
git commit -m "feat(tmbx): journaling decorators for the legacy patcher and submitter"
```

---

### Task 7: Wire the decorators into the agent

**Files:**
- Modify: `src/fateforger/agents/timeboxing/agent.py:445-446`
- Test: `tests/unit/test_agent_journal_wiring.py`

**Interfaces:**
- Consumes: `JournalingPatcher`, `JournalingSubmitter` (Task 6); `init_journal`, `JournalStore` (Task 2)
- Produces: a live agent whose patcher and submitter are journaled. Nothing later depends on this task.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_journal_wiring.py
"""The agent's patcher and submitter must be journal-wrapped."""
from __future__ import annotations

import inspect

from fateforger.agents.timeboxing import agent as agent_module


def test_agent_module_imports_journaling_decorators():
    src = inspect.getsource(agent_module)
    assert "JournalingPatcher" in src
    assert "JournalingSubmitter" in src


def test_journal_is_optional_and_failure_is_tolerated():
    """A journal that cannot be opened must not stop the agent from starting."""
    src = inspect.getsource(agent_module._build_journal_store)
    assert "except Exception" in src
    assert "return None" in src


def test_journal_store_is_built_without_touching_the_event_loop():
    """The constructor runs inside a live loop; blocking calls would raise there.

    Because the failure path degrades to None, a blocking call would leave the
    journal silently disabled in production while this suite stayed green.
    """
    src = inspect.getsource(agent_module._build_journal_store)
    assert "run_until_complete" not in src
    assert "asyncio.run" not in src
    assert "journal_sessionmaker" in src


async def test_build_journal_store_works_inside_a_running_loop():
    """Exercise the real constraint rather than asserting on source text."""
    agent_module._JOURNAL_STORE = None
    try:
        assert agent_module._build_journal_store() is not None
    finally:
        agent_module._JOURNAL_STORE = None


def test_wrappers_are_skipped_when_journal_unavailable():
    assert agent_module._maybe_journal_patcher(sentinel := object(), None) is sentinel
    assert agent_module._maybe_journal_submitter(sentinel2 := object(), None) is sentinel2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_agent_journal_wiring.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_build_journal_store'`

- [ ] **Step 3: Add the wiring helpers**

Add near the top of `src/fateforger/agents/timeboxing/agent.py`, after the existing imports:

```python
from tmbx.journal.instrument import JournalingPatcher, JournalingSubmitter
from tmbx.journal.store import JournalStore, journal_sessionmaker

_JOURNAL_STORE: JournalStore | None = None


def _build_journal_store() -> JournalStore | None:
    """Open the patch journal, or return ``None`` if unavailable.

    Synchronous by construction. This runs from ``__init__`` while the Slack
    bot's event loop is already running, so it must never call ``asyncio.run``
    or ``run_until_complete`` — both raise inside a running loop, and because
    the failure path degrades to ``None``, the journal would be silently
    disabled in production while every test passed.

    ``journal_sessionmaker`` only builds a lazily-connecting engine. The schema
    is created out of band by ``tmbx-init-journal``; if it is missing, the
    first append fails, is logged by the decorator's guard, and planning
    continues.
    """
    global _JOURNAL_STORE
    if _JOURNAL_STORE is not None:
        return _JOURNAL_STORE
    try:
        _JOURNAL_STORE = JournalStore(journal_sessionmaker())
        return _JOURNAL_STORE
    except Exception:
        logger.warning("patch journal unavailable; continuing unjournaled", exc_info=True)
        return None


def _maybe_journal_patcher(patcher: Any, store: JournalStore | None) -> Any:
    """Wrap the patcher when a journal is available, else pass it through."""
    if store is None:
        return patcher
    return JournalingPatcher(patcher, store)


def _maybe_journal_submitter(submitter: Any, store: JournalStore | None) -> Any:
    """Wrap the submitter when a journal is available, else pass it through."""
    if store is None:
        return submitter
    return JournalingSubmitter(submitter, store)
```

- [ ] **Step 4: Change the two construction lines**

Replace `agent.py:445-446`:

```python
        self._timebox_patcher = TimeboxPatcher()
        self._calendar_submitter = CalendarSubmitter()
```

with:

```python
        _journal = _build_journal_store()
        self._timebox_patcher = _maybe_journal_patcher(TimeboxPatcher(), _journal)
        self._calendar_submitter = _maybe_journal_submitter(
            CalendarSubmitter(), _journal
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/test_agent_journal_wiring.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Verify the timeboxing suite still passes**

Run: `poetry run pytest tests/unit -k timeboxing -v`
Expected: PASS — the decorators pass unknown attributes through via `__getattr__`, so existing behaviour is unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/agents/timeboxing/agent.py tests/unit/test_agent_journal_wiring.py
git commit -m "feat(timeboxing): journal patch attempts, commits and undos"
```

---

### Task 8: Read API for the constraint memory map

**Files:**
- Create: `src/tmbx/journal/read_api.py`
- Test: `tests/unit/tmbx/test_read_api.py`

**Interfaces:**
- Consumes: `JournalStore` (Task 2), `derive_dispositions` (Task 3)
- Produces: `PatchRecord` (Pydantic) and `JournalReader(store).records(calendar_id, start, end) -> list[PatchRecord]`

This is the feedback channel map B consumes. It reads rather than tailing the table, so the schema can change without breaking them.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_read_api.py
from __future__ import annotations

from datetime import date

import pytest

from tmbx.journal.disposition import Disposition
from tmbx.journal.models import ConstraintRef, EntryKind, JournalEntry, PatchOutcome
from tmbx.journal.read_api import JournalReader
from tmbx.journal.store import JournalStore, init_journal


@pytest.fixture
async def reader(tmp_path):
    store = JournalStore(await init_journal(tmp_path / "j.db"))

    attempt = JournalEntry(
        calendar_id="primary",
        plan_date=date(2026, 8, 17),
        instruction="move lunch",
        ops_json='{"ops":[]}',
        ops_schema_version=1,
        outcome=PatchOutcome.APPLIED,
        kind=EntryKind.ATTEMPT,
    )
    attempt.set_constraints(
        [ConstraintRef(uid="c1", uid_kind="minted", reason="graphflow_turn")]
    )
    await store.append(attempt)
    await store.append(
        JournalEntry(
            calendar_id="primary",
            plan_date=date(2026, 8, 17),
            ops_json="{}",
            ops_schema_version=1,
            outcome=PatchOutcome.APPLIED,
            kind=EntryKind.COMMIT,
            tx_id="tx1",
        )
    )
    return JournalReader(store)


async def test_records_carry_derived_disposition(reader):
    records = await reader.records("primary", date(2026, 8, 17), date(2026, 8, 17))
    by_kind = {r.kind: r for r in records}
    assert by_kind["attempt"].disposition == Disposition.ABANDONED
    assert by_kind["commit"].disposition == Disposition.ACCEPTED


async def test_records_carry_constraint_refs_with_provenance(reader):
    records = await reader.records("primary", date(2026, 8, 17), date(2026, 8, 17))
    attempt = next(r for r in records if r.kind == "attempt")
    assert attempt.constraints[0].uid == "c1"
    assert attempt.constraints[0].uid_kind == "minted"
    assert attempt.constraints[0].reason == "graphflow_turn"


async def test_date_range_is_inclusive(reader):
    assert await reader.records("primary", date(2026, 8, 18), date(2026, 8, 19)) == []
    assert len(await reader.records("primary", date(2026, 8, 16), date(2026, 8, 18))) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_read_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.journal.read_api'`

- [ ] **Step 3: Write the implementation**

```python
# src/tmbx/journal/read_api.py
"""Read API over the journal — the feedback channel for constraint memory.

Consumers read this rather than the table, so storage can change underneath
them. Disposition is computed here, never stored.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta

from pydantic import BaseModel

from .disposition import Disposition, derive_dispositions
from .models import ConstraintRef
from .store import JournalStore


class PatchRecord(BaseModel):
    """One journal row, flattened, with its derived disposition."""

    id: int
    created_at: datetime
    kind: str
    calendar_id: str
    plan_date: date_type
    instruction: str | None
    constraints: list[ConstraintRef]
    ops_json: str
    ops_schema_version: int
    outcome: str
    disposition: Disposition
    tx_id: str | None
    undoes_tx: str | None


class JournalReader:
    """Read journal rows with dispositions resolved."""

    def __init__(self, store: JournalStore) -> None:
        self._store = store

    async def records(
        self, calendar_id: str, start: date_type, end: date_type
    ) -> list[PatchRecord]:
        """Return records for an inclusive date range.

        Dispositions are derived per day, since supersession is a within-day
        relation.
        """
        out: list[PatchRecord] = []
        day = start
        while day <= end:
            entries = await self._store.by_day(calendar_id, day)
            dispositions = derive_dispositions(entries)
            for entry in entries:
                if entry.id is None:
                    continue
                out.append(
                    PatchRecord(
                        id=entry.id,
                        created_at=entry.created_at,
                        kind=entry.kind.value,
                        calendar_id=entry.calendar_id,
                        plan_date=entry.plan_date,
                        instruction=entry.instruction,
                        constraints=entry.get_constraints(),
                        ops_json=entry.ops_json,
                        ops_schema_version=entry.ops_schema_version,
                        outcome=entry.outcome.value,
                        disposition=dispositions[entry.id],
                        tx_id=entry.tx_id,
                        undoes_tx=entry.undoes_tx,
                    )
                )
            day = day + timedelta(days=1)
        return out


__all__ = ["JournalReader", "PatchRecord"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_read_api.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Run the full tmbx suite**

Run: `poetry run pytest tests/unit/tmbx -v`
Expected: PASS — all tasks 1–8.

- [ ] **Step 6: Commit**

```bash
git add src/tmbx/journal/read_api.py tests/unit/tmbx/test_read_api.py
git commit -m "feat(tmbx): journal read API with derived dispositions"
```

**Part A is complete.** The journal is filling. Report the read API shape to the constraint memory session before starting Part B.

---

# Part B — Level 1 op vocabulary and MCP server (#146)

Clean-room. Nothing here imports `fateforger`.

---

### Task 9: Core plan models with three-level identity

**Files:**
- Create: `src/tmbx/core/__init__.py`
- Create: `src/tmbx/core/models.py`
- Test: `tests/unit/tmbx/test_core_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `ET` enum — `M, C, DW, SW, PR, H, R, BU, BG`
  - `AfterPrev(a="ap", dur)`, `BeforeNext(a="bn", dur)`, `FixedStart(a="fs", st, dur)`, `FixedWindow(a="fw", st, et)`, and `Timing` as their discriminated union on `a`
  - `Block(uid, h, slug, n, d, t, p, anchor_source)` — `h` is the addressing handle
  - `Plan(blocks, date, tz)` with `resolve() -> list[Resolved]`
  - `Resolved(uid, h, n, t, start, end, dur)`

Handle format: 3–5 uppercase letters followed by 1–2 digits, validated by regex. Assigned by the model, persisted by the server, unique per plan.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_core_models.py
from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from pydantic import ValidationError

from tmbx.core.models import ET, AfterPrev, Block, FixedStart, FixedWindow, Plan


def _block(h, t=ET.DW, p=None, uid=None, n="Work"):
    return Block(uid=uid or f"u-{h}", h=h, n=n, t=t, p=p or AfterPrev(dur=timedelta(minutes=60)))


def test_handle_format_accepted():
    assert _block("DW1").h == "DW1"
    assert _block("GYM12").h == "GYM12"


@pytest.mark.parametrize("bad", ["dw1", "D1", "TOOLONG1", "DW", "DW123", "DW-1"])
def test_handle_format_rejected(bad):
    with pytest.raises(ValidationError):
        _block(bad)


def test_resolve_chains_after_prev_from_a_fixed_anchor():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("PR1", t=ET.PR, p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
            _block("DW1", p=AfterPrev(dur=timedelta(minutes=90))),
        ],
    )
    resolved = plan.resolve()
    assert (resolved[0].start, resolved[0].end) == (time(9, 0), time(9, 30))
    assert (resolved[1].start, resolved[1].end) == (time(9, 30), time(11, 0))


def test_fixed_window_infers_duration():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[_block("M1", t=ET.M, p=FixedWindow(st=time(11, 0), et=time(11, 45)))],
    )
    assert plan.resolve()[0].dur == timedelta(minutes=45)


def test_chain_requires_an_anchor():
    with pytest.raises(ValidationError):
        Plan(
            date=date(2026, 8, 17),
            blocks=[_block("DW1"), _block("DW2")],
        )


def test_handles_must_be_unique_within_a_plan():
    with pytest.raises(ValidationError):
        Plan(
            date=date(2026, 8, 17),
            blocks=[
                _block("DW1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30))),
                _block("DW1"),
            ],
        )


def test_overlap_is_rejected():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("DW1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=120))),
            _block("DW2", p=FixedStart(st=time(10, 0), dur=timedelta(minutes=60))),
        ],
    )
    with pytest.raises(ValueError, match="Overlap"):
        plan.resolve()


def test_background_events_are_exempt_from_overlap():
    plan = Plan(
        date=date(2026, 8, 17),
        blocks=[
            _block("DW1", p=FixedStart(st=time(9, 0), dur=timedelta(minutes=120))),
            _block("BG1", t=ET.BG, p=FixedWindow(st=time(9, 30), et=time(10, 0))),
        ],
    )
    assert len(plan.resolve()) == 2


def test_background_must_use_fixed_timing():
    with pytest.raises(ValidationError):
        _block("BG1", t=ET.BG, p=AfterPrev(dur=timedelta(minutes=30)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_core_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.core'`

- [ ] **Step 3: Write the models**

```python
# src/tmbx/core/__init__.py
"""Pure timebox domain — no I/O, no LLM, no network."""

from __future__ import annotations

__all__: list[str] = []
```

```python
# src/tmbx/core/models.py
"""Plan and block models with three-level identity.

Identity is deliberately three concepts:

* ``uid``  — instance identity. Server-minted, opaque, durable, never shown
  to the model. Survives rename, retime, reorder.
* ``slug`` — pattern identity. Names the recurring *kind* of block, stable
  across days. What memory anchors attach to.
* ``h``    — addressing handle. Model-assigned, persisted by the server,
  re-rendered every turn, valid for the turn it was rendered in.

The four-mode time grammar is carried over unchanged: the model states
intent, ``Plan.resolve()`` does the arithmetic.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Annotated, Literal, Union

from isodate import parse_duration as _parse_duration
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

def is_valid_handle(value: str) -> bool:
    """Check a handle's shape: 3-5 uppercase letters then 1-2 digits.

    Written without ``re`` because project rules ban the module outright.
    A handle is an identifier the system mints, not user prose, so checking
    its shape is not a judgement about meaning — but the ban is absolute,
    so this uses plain string predicates.
    """
    digits = value[len(value.rstrip("0123456789")) :]
    letters = value[: len(value) - len(digits)]
    return (
        3 <= len(letters) <= 5
        and 1 <= len(digits) <= 2
        and letters.isalpha()
        and letters.isupper()
        and digits.isdigit()
    )


class ET(str, Enum):
    """Event type."""

    M = "M"
    C = "C"
    DW = "DW"
    SW = "SW"
    PR = "PR"
    H = "H"
    R = "R"
    BU = "BU"
    BG = "BG"


def _coerce_duration(value: object) -> object:
    return _parse_duration(value) if isinstance(value, str) else value


def _coerce_time(value: object) -> object:
    return time.fromisoformat(value) if isinstance(value, str) else value


class AfterPrev(BaseModel):
    """Duration only; starts when the previous block ends."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["ap"] = "ap"
    dur: timedelta

    _d = field_validator("dur", mode="before")(lambda cls, v: _coerce_duration(v))


class BeforeNext(BaseModel):
    """Duration only; ends when the next block starts."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["bn"] = "bn"
    dur: timedelta

    _d = field_validator("dur", mode="before")(lambda cls, v: _coerce_duration(v))


class FixedStart(BaseModel):
    """Pinned start, inferred end."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["fs"] = "fs"
    st: time
    dur: timedelta

    _t = field_validator("st", mode="before")(lambda cls, v: _coerce_time(v))
    _d = field_validator("dur", mode="before")(lambda cls, v: _coerce_duration(v))


class FixedWindow(BaseModel):
    """Pinned start and end; duration is an output."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["fw"] = "fw"
    st: time
    et: time

    _s = field_validator("st", mode="before")(lambda cls, v: _coerce_time(v))
    _e = field_validator("et", mode="before")(lambda cls, v: _coerce_time(v))


Timing = Annotated[
    Union[AfterPrev, BeforeNext, FixedStart, FixedWindow],
    Field(discriminator="a"),
]

AnchorSource = Literal["user", "constraint", "calendar"]


class Block(BaseModel):
    """One timeboxed block."""

    model_config = ConfigDict(extra="forbid")

    uid: str = Field(description="Server-minted instance identity")
    h: str = Field(description="Addressing handle, e.g. DW1")
    slug: str | None = Field(default=None, description="Recurring block kind")
    n: str = Field(description="Name / summary")
    d: str = Field(default="", description="Short description")
    t: ET
    p: Timing
    anchor_source: AnchorSource | None = Field(
        default=None,
        description="Why this block is pinned. Required when p is fs or fw.",
    )

    @field_validator("h")
    @classmethod
    def _handle_shape(cls, value: str) -> str:
        if not is_valid_handle(value):
            raise ValueError(
                f"handle {value!r} must be 3-5 uppercase letters then 1-2 digits"
            )
        return value

    @model_validator(mode="after")
    def _background_needs_fixed_timing(self) -> "Block":
        if self.t is ET.BG and self.p.a not in ("fs", "fw"):
            raise ValueError("BG blocks require fs or fw timing")
        return self


class Resolved(BaseModel):
    """A block with concrete times computed."""

    uid: str
    h: str
    n: str
    t: ET
    mode: str
    start: time
    end: time
    dur: timedelta


class Plan(BaseModel):
    """A day's plan."""

    model_config = ConfigDict(extra="forbid")

    blocks: list[Block] = Field(default_factory=list)
    date: date_type
    tz: str = "Europe/Amsterdam"

    @model_validator(mode="after")
    def _handles_unique(self) -> "Plan":
        seen = [b.h for b in self.blocks]
        dupes = {h for h in seen if seen.count(h) > 1}
        if dupes:
            raise ValueError(f"duplicate handles: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _chain_is_anchored(self) -> "Plan":
        chain = [b for b in self.blocks if b.t is not ET.BG]
        if chain and not any(b.p.a in ("fs", "fw") for b in chain):
            raise ValueError("chain needs at least one fs or fw anchor")
        return self

    def by_handle(self, handle: str) -> Block | None:
        """Look up a block by its addressing handle."""
        return next((b for b in self.blocks if b.h == handle), None)

    def resolve(self, *, check_overlap: bool = True) -> list[Resolved]:
        """Compute concrete start/end for every block.

        Forward pass handles ap/fs/fw; a backward pass closes bn.
        """
        day = self.date
        rows: list[dict] = []
        last_end: datetime | None = None

        for block in self.blocks:
            row: dict = {
                "uid": block.uid,
                "h": block.h,
                "n": block.n,
                "t": block.t,
                "mode": block.p.a,
            }
            p = block.p

            if p.a == "ap":
                if last_end is None:
                    raise ValueError(f"{block.h}: after_previous has no preceding block")
                start_dt, end_dt = last_end, last_end + p.dur
            elif p.a == "fs":
                start_dt = datetime.combine(day, p.st)
                end_dt = start_dt + p.dur
            elif p.a == "fw":
                start_dt = datetime.combine(day, p.st)
                end_dt = datetime.combine(day, p.et)
            else:  # bn — resolved backwards
                row["_pending_dur"] = p.dur
                rows.append(row)
                continue

            row.update(start=start_dt.time(), end=end_dt.time(), dur=end_dt - start_dt)
            last_end = end_dt
            rows.append(row)

        next_start: datetime | None = None
        for row in reversed(rows):
            if "_pending_dur" in row:
                if next_start is None:
                    raise ValueError(f"{row['h']}: before_next has no following block")
                dur = row.pop("_pending_dur")
                start_dt = next_start - dur
                row.update(start=start_dt.time(), end=next_start.time(), dur=dur)
            next_start = datetime.combine(day, row["start"])

        resolved = [Resolved(**row) for row in rows]

        if check_overlap:
            chain = [r for r in resolved if r.t is not ET.BG]
            for a, b in zip(chain, chain[1:]):
                if datetime.combine(day, a.end) > datetime.combine(day, b.start):
                    raise ValueError(
                        f"Overlap: {a.h} ends {a.end} but {b.h} starts {b.start}"
                    )

        return resolved


__all__ = [
    "AfterPrev",
    "AnchorSource",
    "BeforeNext",
    "Block",
    "ET",
    "FixedStart",
    "FixedWindow",
    "is_valid_handle",
    "Plan",
    "Resolved",
    "Timing",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_core_models.py -v`
Expected: PASS, 14 tests (9 named plus 6 parametrised cases minus overlap)

- [ ] **Step 5: Commit**

```bash
git add src/tmbx/core tests/unit/tmbx/test_core_models.py
git commit -m "feat(tmbx): plan and block models with three-level identity"
```

---

### Task 10: Level 1 ops with set semantics

**Files:**
- Create: `src/tmbx/core/ops.py`
- Test: `tests/unit/tmbx/test_ops.py`

**Interfaces:**
- Consumes: `Block`, `Plan`, `Timing`, `ET` (Task 9)
- Produces:
  - `AddBlock(op="add", after, n, d, t, p, h, slug, why)`, `RemoveBlock(op="remove", h, why)`, `UpdateBlock(op="update", h, n, d, t, p, slug, why)`, `MoveBlock(op="move", h, after, why)`
  - `Op` union discriminated on `op`; `Patch(ops)`
  - `validate_patch(plan, patch) -> list[str]` — static errors, empty when valid
  - `apply_ops(plan, patch, mint_uid) -> Plan`

**Set semantics:** every op resolves against the pre-patch plan. Order is irrelevant. `after` is a handle or `None` (prepend); the sentinel `"END"` appends. Removals are applied first, then updates in place, then moves, then adds — but because all *addressing* resolves against the pre-patch plan, the result does not depend on the order ops appear in.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_ops.py
from __future__ import annotations

import itertools
from datetime import date, time, timedelta

import pytest

from tmbx.core.models import ET, AfterPrev, Block, FixedStart, Plan
from tmbx.core.ops import (
    AddBlock,
    MoveBlock,
    Patch,
    RemoveBlock,
    UpdateBlock,
    apply_ops,
    validate_patch,
)


def _mint(seq=itertools.count(1)):
    return f"u-new-{next(seq)}"


def _plan():
    return Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
                  p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                  anchor_source="user"),
            Block(uid="u2", h="DW1", n="Sprint", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
            Block(uid="u3", h="DW2", n="Review", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=45))),
        ],
    )


def test_remove_by_handle():
    result = apply_ops(_plan(), Patch(ops=[RemoveBlock(h="DW1")]), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "DW2"]


def test_update_merges_only_given_fields():
    result = apply_ops(_plan(), Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]), mint_uid=_mint)
    block = result.by_handle("DW1")
    assert block.n == "Renamed"
    assert block.t is ET.DW
    assert block.uid == "u2"


def test_update_preserves_uid_and_handle():
    result = apply_ops(
        _plan(),
        Patch(ops=[UpdateBlock(h="DW1", p=AfterPrev(dur=timedelta(minutes=30)))]),
        mint_uid=_mint,
    )
    assert result.by_handle("DW1").uid == "u2"


def test_add_after_handle():
    op = AddBlock(after="PR1", h="BU1", n="Buffer", t=ET.BU,
                  p=AfterPrev(dur=timedelta(minutes=10)))
    result = apply_ops(_plan(), Patch(ops=[op]), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "BU1", "DW1", "DW2"]
    assert result.by_handle("BU1").uid.startswith("u-new-")


def test_add_at_end_with_sentinel():
    op = AddBlock(after="END", h="SHU1", n="Shutdown", t=ET.PR,
                  p=AfterPrev(dur=timedelta(minutes=15)))
    result = apply_ops(_plan(), Patch(ops=[op]), mint_uid=_mint)
    assert result.blocks[-1].h == "SHU1"


def test_move_by_anchor():
    result = apply_ops(_plan(), Patch(ops=[MoveBlock(h="DW2", after="PR1")]), mint_uid=_mint)
    assert [b.h for b in result.blocks] == ["PR1", "DW2", "DW1"]


def test_ops_are_commutative():
    """The whole point of set semantics: op order must not change the result."""
    ops = [
        RemoveBlock(h="DW2"),
        UpdateBlock(h="DW1", n="Renamed"),
        AddBlock(after="PR1", h="BU1", n="Buffer", t=ET.BU,
                 p=AfterPrev(dur=timedelta(minutes=10))),
    ]
    results = set()
    for permutation in itertools.permutations(ops):
        plan = apply_ops(_plan(), Patch(ops=list(permutation)), mint_uid=lambda: "u-fixed")
        results.add(tuple((b.h, b.n) for b in plan.blocks))
    assert len(results) == 1


def test_validate_rejects_unknown_handle():
    errors = validate_patch(_plan(), Patch(ops=[RemoveBlock(h="NOPE1")]))
    assert any("NOPE1" in e for e in errors)


def test_validate_rejects_two_ops_touching_one_block():
    errors = validate_patch(
        _plan(), Patch(ops=[UpdateBlock(h="DW1", n="A"), RemoveBlock(h="DW1")])
    )
    assert any("DW1" in e for e in errors)


def test_validate_rejects_duplicate_new_handle():
    op = AddBlock(after="END", h="DW1", n="Clash", t=ET.DW,
                  p=AfterPrev(dur=timedelta(minutes=30)))
    assert any("DW1" in e for e in validate_patch(_plan(), Patch(ops=[op])))


def test_validate_requires_anchor_source_for_fixed_timing():
    op = AddBlock(after="END", h="APP1", n="Appointment", t=ET.M,
                  p=FixedStart(st=time(16, 0), dur=timedelta(minutes=30)))
    assert any("anchor_source" in e for e in validate_patch(_plan(), Patch(ops=[op])))


def test_apply_raises_on_invalid_patch():
    with pytest.raises(ValueError):
        apply_ops(_plan(), Patch(ops=[RemoveBlock(h="NOPE1")]), mint_uid=_mint)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_ops.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.core.ops'`

- [ ] **Step 3: Write the implementation**

```python
# src/tmbx/core/ops.py
"""Level 1 op vocabulary.

A patch is a SET: every op resolves against the pre-patch plan, so op order
is irrelevant and a patch is verifiable before it is applied. Sequencing is
real only across patches, at the transaction boundary.

Addressing is by handle. Never by index — an index-addressed op is
meaningless against any other plan and therefore useless as a training
example.
"""

from __future__ import annotations

from typing import Annotated, Callable, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .models import Block, ET, Plan, Timing

END = "END"


class _OpBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    why: str | None = Field(
        default=None, description="Why this op — feeds the journal and memory anchors"
    )


class AddBlock(_OpBase):
    """Insert a new block after an existing one."""

    op: Literal["add"] = "add"
    after: str | None = Field(
        default=END, description="Handle to insert after; None prepends; 'END' appends"
    )
    h: str = Field(description="Handle for the new block")
    n: str
    d: str = ""
    t: ET
    p: Timing
    slug: str | None = None
    anchor_source: str | None = None


class RemoveBlock(_OpBase):
    op: Literal["remove"] = "remove"
    h: str


class UpdateBlock(_OpBase):
    """Merge the given fields onto an existing block. Unset fields are untouched."""

    op: Literal["update"] = "update"
    h: str
    n: str | None = None
    d: str | None = None
    t: ET | None = None
    p: Timing | None = None
    slug: str | None = None
    anchor_source: str | None = None


class MoveBlock(_OpBase):
    op: Literal["move"] = "move"
    h: str
    after: str | None = Field(default=END)


Op = Annotated[
    Union[AddBlock, RemoveBlock, UpdateBlock, MoveBlock],
    Field(discriminator="op"),
]


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ops: list[Op] = Field(min_length=1)


def validate_patch(plan: Plan, patch: Patch) -> list[str]:
    """Check a patch against a plan without applying it.

    Set semantics make this possible: nothing depends on partial application.
    """
    errors: list[str] = []
    existing = {b.h for b in plan.blocks}
    touched: set[str] = set()
    added: set[str] = set()

    for index, op in enumerate(patch.ops):
        if isinstance(op, AddBlock):
            if op.h in existing or op.h in added:
                errors.append(f"op {index}: handle {op.h} already exists")
            added.add(op.h)
            if op.after not in (None, END) and op.after not in existing:
                errors.append(f"op {index}: anchor {op.after} not found")
            if op.p.a in ("fs", "fw") and not op.anchor_source:
                errors.append(
                    f"op {index}: {op.h} uses fixed timing without anchor_source"
                )
            continue

        if op.h not in existing:
            errors.append(f"op {index}: handle {op.h} not found")
            continue
        if op.h in touched:
            errors.append(f"op {index}: {op.h} is touched by more than one op")
        touched.add(op.h)

        if isinstance(op, MoveBlock) and op.after not in (None, END):
            if op.after not in existing:
                errors.append(f"op {index}: anchor {op.after} not found")
        if isinstance(op, UpdateBlock) and op.p is not None:
            if op.p.a in ("fs", "fw") and not op.anchor_source:
                errors.append(
                    f"op {index}: {op.h} set to fixed timing without anchor_source"
                )

    return errors


def _insert(blocks: list[Block], block: Block, after: str | None) -> None:
    """Insert respecting the anchor, which refers to pre-patch position."""
    if after is None:
        blocks.insert(0, block)
        return
    if after == END:
        blocks.append(block)
        return
    for position, existing in enumerate(blocks):
        if existing.h == after:
            blocks.insert(position + 1, block)
            return
    blocks.append(block)


def apply_ops(plan: Plan, patch: Patch, *, mint_uid: Callable[[], str]) -> Plan:
    """Apply a patch and return a new plan.

    Args:
        plan: Pre-patch plan. Not mutated.
        patch: Ops to apply. All addressing resolves against ``plan``.
        mint_uid: Called once per added block to produce an opaque uid.

    Raises:
        ValueError: If ``validate_patch`` finds anything.
    """
    errors = validate_patch(plan, patch)
    if errors:
        raise ValueError("invalid patch: " + "; ".join(errors))

    blocks = [b.model_copy(deep=True) for b in plan.blocks]

    for op in patch.ops:
        if isinstance(op, RemoveBlock):
            blocks = [b for b in blocks if b.h != op.h]

    for op in patch.ops:
        if isinstance(op, UpdateBlock):
            target = next((b for b in blocks if b.h == op.h), None)
            if target is None:
                continue
            updates = {
                key: value
                for key, value in (
                    ("n", op.n),
                    ("d", op.d),
                    ("t", op.t),
                    ("p", op.p),
                    ("slug", op.slug),
                    ("anchor_source", op.anchor_source),
                )
                if value is not None
            }
            merged = target.model_copy(update=updates)
            Block.model_validate(merged.model_dump())
            blocks[blocks.index(target)] = merged

    for op in patch.ops:
        if isinstance(op, MoveBlock):
            target = next((b for b in blocks if b.h == op.h), None)
            if target is None:
                continue
            blocks.remove(target)
            _insert(blocks, target, op.after)

    for op in patch.ops:
        if isinstance(op, AddBlock):
            _insert(
                blocks,
                Block(
                    uid=mint_uid(),
                    h=op.h,
                    slug=op.slug,
                    n=op.n,
                    d=op.d,
                    t=op.t,
                    p=op.p,
                    anchor_source=op.anchor_source,  # type: ignore[arg-type]
                ),
                op.after,
            )

    return Plan(blocks=blocks, date=plan.date, tz=plan.tz)


__all__ = [
    "AddBlock",
    "END",
    "MoveBlock",
    "Op",
    "Patch",
    "RemoveBlock",
    "UpdateBlock",
    "apply_ops",
    "validate_patch",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_ops.py -v`
Expected: PASS, 12 tests. The commutativity test is the load-bearing one.

- [ ] **Step 5: Commit**

```bash
git add src/tmbx/core/ops.py tests/unit/tmbx/test_ops.py
git commit -m "feat(tmbx): level 1 ops with set semantics and handle addressing"
```

---

### Task 11: Over-specification detection

**Files:**
- Create: `src/tmbx/core/commitment.py`
- Test: `tests/unit/tmbx/test_commitment.py`

**Interfaces:**
- Consumes: `Plan`, `Block`, `AfterPrev`, `FixedStart`, `FixedWindow` (Task 9)
- Produces: `overspecified(plan) -> list[str]` — handles whose fixed timing could be relaxed to `ap` without changing any resolved time

This makes "specify as little as possible" measurable rather than aspirational, and gives the prompt compiler a metric component.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_commitment.py
from __future__ import annotations

from datetime import date, time, timedelta

from tmbx.core.commitment import overspecified
from tmbx.core.models import ET, AfterPrev, Block, FixedStart, Plan


def _p(**kw):
    return Plan(date=date(2026, 8, 17), **kw)


def test_redundant_fixed_start_is_flagged():
    """DW1 at 09:30 is exactly where ap would put it — the pin buys nothing."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(9, 30), dur=timedelta(minutes=90)), anchor_source="user"),
    ])
    assert overspecified(plan) == ["DW1"]


def test_load_bearing_fixed_start_is_not_flagged():
    """DW1 at 11:00 leaves a gap ap cannot reproduce."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW,
              p=FixedStart(st=time(11, 0), dur=timedelta(minutes=90)), anchor_source="user"),
    ])
    assert overspecified(plan) == []


def test_first_anchor_is_never_flagged():
    """Removing the only anchor would leave the chain unanchored."""
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
    ])
    assert overspecified(plan) == []


def test_already_minimal_plan_is_clean():
    plan = _p(blocks=[
        Block(uid="u1", h="PR1", n="Plan", t=ET.PR,
              p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)), anchor_source="user"),
        Block(uid="u2", h="DW1", n="Work", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=90))),
        Block(uid="u3", h="DW2", n="More", t=ET.DW, p=AfterPrev(dur=timedelta(minutes=45))),
    ])
    assert overspecified(plan) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_commitment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.core.commitment'`

- [ ] **Step 3: Write the implementation**

```python
# src/tmbx/core/commitment.py
"""Detect over-specified timing.

Least commitment says: use the weakest mode that expresses the intent. A
fixed start that lands exactly where ``ap`` would have put the block bought
nothing, and every gratuitous pin ossifies the chain so downstream blocks
stop shifting — which is how buffer and constraint policies quietly stop
applying.

This check turns that principle into a measurement.
"""

from __future__ import annotations

from .models import AfterPrev, Plan


def overspecified(plan: Plan) -> list[str]:
    """Handles whose fixed timing could be relaxed to ``ap`` with no effect.

    The first anchor in the chain is never flagged — removing it would leave
    the chain unanchored.
    """
    try:
        rows = plan.resolve(check_overlap=False)
    except ValueError:
        return []

    baseline = {r.h: (r.start, r.end) for r in rows}
    durations = {r.h: r.dur for r in rows}

    flagged: list[str] = []
    seen_anchor = False

    for index, block in enumerate(plan.blocks):
        if block.p.a not in ("fs", "fw"):
            continue
        if not seen_anchor:
            seen_anchor = True
            continue

        candidate = plan.model_copy(deep=True)
        candidate.blocks[index] = candidate.blocks[index].model_copy(
            update={"p": AfterPrev(dur=durations[block.h]), "anchor_source": None}
        )

        try:
            relaxed = {
                r.h: (r.start, r.end) for r in candidate.resolve(check_overlap=False)
            }
        except ValueError:
            continue

        if relaxed == baseline:
            flagged.append(block.h)

    return flagged


__all__ = ["overspecified"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_commitment.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add src/tmbx/core/commitment.py tests/unit/tmbx/test_commitment.py
git commit -m "feat(tmbx): detect over-specified timing as a measurable check"
```

---

### Task 12: Plan rendering

**Files:**
- Create: `src/tmbx/core/render.py`
- Test: `tests/unit/tmbx/test_render.py`

**Interfaces:**
- Consumes: `Plan`, `Resolved` (Task 9)
- Produces: `render_plan(plan) -> str` — a TOON-style table with columns `H, type, summary, ST, ET, mode, dur, location`

Three deltas from the legacy `timebox_events_rows`: `H` becomes the first column because it is the addressing key; the boolean anchor flag becomes the actual mode, since a boolean collapses four modes into two and hides exactly what least-commitment depends on; and durations are ISO rather than `total_seconds()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_render.py
from __future__ import annotations

from datetime import date, time, timedelta

from tmbx.core.models import ET, AfterPrev, Block, FixedWindow, Plan
from tmbx.core.render import render_plan


def _plan():
    return Plan(
        date=date(2026, 8, 17),
        blocks=[
            Block(uid="u1", h="DW1", n="Sprint", t=ET.DW,
                  p=FixedWindow(st=time(9, 0), et=time(10, 30)), anchor_source="user"),
            Block(uid="u2", h="LUN1", n="Lunch", t=ET.R,
                  p=AfterPrev(dur=timedelta(minutes=45))),
        ],
    )


def test_handle_is_the_first_column():
    header = render_plan(_plan()).splitlines()[0]
    assert header.split(",")[0].strip().endswith("H")


def test_all_four_modes_are_distinguishable():
    body = render_plan(_plan())
    assert "fw" in body
    assert "ap" in body
    assert "true" not in body and "false" not in body


def test_durations_are_iso_not_seconds():
    body = render_plan(_plan())
    assert "PT45M" in body
    assert "2700" not in body


def test_resolved_times_are_present():
    body = render_plan(_plan())
    assert "09:00" in body and "10:30" in body


def test_uid_is_never_rendered():
    body = render_plan(_plan())
    assert "u1" not in body and "u2" not in body


def test_empty_plan_renders_header_only():
    plan = Plan(date=date(2026, 8, 17), blocks=[])
    assert len(render_plan(plan).strip().splitlines()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.core.render'`

- [ ] **Step 3: Write the implementation**

```python
# src/tmbx/core/render.py
"""Render a plan for the model.

Resolved times AND structure. Resolved-only would force the model to write
fixed times because that is all it can see; structural-only would force it to
compute "what is after 14:00", which is the arithmetic we removed from its
job.

``uid`` is never rendered — the handle stands in for it.
"""

from __future__ import annotations

from datetime import timedelta

from .models import Plan

COLUMNS = ("H", "type", "summary", "ST", "ET", "mode", "dur")


def _iso_duration(value: timedelta) -> str:
    """Format a timedelta as an ISO 8601 duration."""
    total = int(value.total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"PT{hours}H{minutes}M"
    if hours:
        return f"PT{hours}H"
    return f"PT{minutes}M"


def render_plan(plan: Plan) -> str:
    """Render the plan as a TOON-style table."""
    lines = [f"plan[{len(plan.blocks)}]{{{','.join(COLUMNS)}}}:"]
    if not plan.blocks:
        return lines[0]

    resolved = {r.h: r for r in plan.resolve(check_overlap=False)}
    for block in plan.blocks:
        row = resolved[block.h]
        lines.append(
            ",".join(
                [
                    block.h,
                    block.t.value,
                    block.n,
                    row.start.strftime("%H:%M"),
                    row.end.strftime("%H:%M"),
                    block.p.a,
                    _iso_duration(row.dur),
                ]
            )
        )
    return "\n".join(lines)


__all__ = ["COLUMNS", "render_plan"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_render.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Run the whole tmbx suite**

Run: `poetry run pytest tests/unit/tmbx -v`
Expected: PASS — Parts A and B so far.

- [ ] **Step 6: Commit**

```bash
git add src/tmbx/core/render.py tests/unit/tmbx/test_render.py
git commit -m "feat(tmbx): plan rendering with handles, real modes, ISO durations"
```

---

### Task 13: Calendar port and snapshot tokens

**Files:**
- Create: `src/tmbx/calendar/__init__.py`
- Create: `src/tmbx/calendar/port.py`
- Create: `src/tmbx/calendar/fake.py`
- Test: `tests/unit/tmbx/test_calendar_port.py`

**Interfaces:**
- Consumes: `Plan`, `Block`, `ET` (Task 9)
- Produces:
  - `CalendarEvent(event_id, summary, description, start, end, etag, uid, handle, slug)` — provider-neutral
  - `CalendarPort` protocol: `async list_day(calendar_id, day) -> list[CalendarEvent]`, `async create(calendar_id, event)`, `async update(calendar_id, event)`, `async delete(calendar_id, event_id)`
  - `Snapshot(token, calendar_id, day, etags: dict[str, str])` and `make_snapshot(calendar_id, day, events) -> Snapshot`
  - `drift(snapshot, live_events) -> list[str]` — event ids whose etag changed, appeared, or vanished
  - `FakeCalendar` implementing `CalendarPort`, with `mutate(event_id)` to simulate a concurrent edit

**Why the fake matters:** the precondition tests in Task 14 need a calendar that changes between snapshot and commit. That is the exact scenario the legacy engine gets wrong, and it cannot be tested against a real calendar.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_calendar_port.py
from __future__ import annotations

from datetime import date, datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent, drift, make_snapshot

DAY = date(2026, 8, 17)


def _event(eid, etag="v1", uid=None, handle=None):
    return CalendarEvent(
        event_id=eid,
        summary="Block",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
        etag=etag,
        uid=uid,
        handle=handle,
    )


@pytest.fixture
def calendar():
    return FakeCalendar({"primary": [_event("e1"), _event("e2")]})


async def test_list_day_returns_events(calendar):
    assert len(await calendar.list_day("primary", DAY)) == 2


async def test_snapshot_captures_etags(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    assert snapshot.etags == {"e1": "v1", "e2": "v1"}
    assert snapshot.token


async def test_no_drift_when_nothing_changed(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == []


async def test_drift_detects_a_concurrent_edit(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    calendar.mutate("e1")
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == ["e1"]


async def test_drift_detects_a_vanished_event(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    await calendar.delete("primary", "e2")
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == ["e2"]


async def test_drift_detects_a_new_event(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    await calendar.create("primary", _event("e3"))
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == ["e3"]


async def test_uid_and_handle_roundtrip(calendar):
    await calendar.create("primary", _event("e9", uid="u9", handle="DW1"))
    stored = next(e for e in await calendar.list_day("primary", DAY) if e.event_id == "e9")
    assert (stored.uid, stored.handle) == ("u9", "DW1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_calendar_port.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.calendar'`

- [ ] **Step 3: Write the port**

```python
# src/tmbx/calendar/__init__.py
"""Provider-neutral calendar access."""

from __future__ import annotations

__all__: list[str] = []
```

```python
# src/tmbx/calendar/port.py
"""Calendar port and snapshot tokens.

A snapshot pins the etag of every observed event. Writes check it first.
The legacy engine writes against a snapshot with no precondition, so an edit
made elsewhere mid-session is silently overwritten; undo replays its
before-state just as blindly and can destroy a newer edit. Both are closed
here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date as date_type
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class CalendarEvent(BaseModel):
    """One calendar event, provider-neutral.

    ``uid``, ``handle`` and ``slug`` live in provider extended properties.
    """

    event_id: str
    summary: str
    description: str = ""
    start: datetime
    end: datetime
    etag: str = ""
    colour_id: str | None = None
    uid: str | None = None
    handle: str | None = None
    slug: str | None = None


class Snapshot(BaseModel):
    """Observed calendar state at a point in time."""

    token: str
    calendar_id: str
    day: date_type
    etags: dict[str, str] = Field(default_factory=dict)
    event_ids: dict[str, str] = Field(
        default_factory=dict,
        description="uid -> provider event id. Keeps uid opaque: never derive "
        "one identifier from the other's string form.",
    )


def make_snapshot(
    calendar_id: str, day: date_type, events: list[CalendarEvent]
) -> Snapshot:
    """Build a snapshot from observed events."""
    etags = {event.event_id: event.etag for event in events}
    event_ids = {event.uid: event.event_id for event in events if event.uid}
    payload = json.dumps(
        {"calendar_id": calendar_id, "day": day.isoformat(), "etags": etags},
        sort_keys=True,
    )
    token = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return Snapshot(
        token=token,
        calendar_id=calendar_id,
        day=day,
        etags=etags,
        event_ids=event_ids,
    )


def drift(snapshot: Snapshot, live: list[CalendarEvent]) -> list[str]:
    """Event ids that changed, appeared, or vanished since the snapshot."""
    live_etags = {event.event_id: event.etag for event in live}
    changed = {
        event_id
        for event_id, etag in live_etags.items()
        if snapshot.etags.get(event_id) != etag
    }
    vanished = set(snapshot.etags) - set(live_etags)
    return sorted(changed | vanished)


class CalendarPort(Protocol):
    """Everything the server needs from a calendar provider."""

    async def list_day(
        self, calendar_id: str, day: date_type
    ) -> list[CalendarEvent]: ...

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent: ...

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent: ...

    async def delete(self, calendar_id: str, event_id: str) -> None: ...


__all__ = ["CalendarEvent", "CalendarPort", "Snapshot", "drift", "make_snapshot"]
```

- [ ] **Step 4: Write the fake**

```python
# src/tmbx/calendar/fake.py
"""In-memory calendar for tests. No network, ever."""

from __future__ import annotations

from datetime import date as date_type

from .port import CalendarEvent


class FakeCalendar:
    """Implements ``CalendarPort`` over a dict."""

    def __init__(self, events: dict[str, list[CalendarEvent]] | None = None) -> None:
        self._events: dict[str, list[CalendarEvent]] = {
            key: [event.model_copy(deep=True) for event in value]
            for key, value in (events or {}).items()
        }
        self._version = 1

    async def list_day(self, calendar_id: str, day: date_type) -> list[CalendarEvent]:
        return [
            event.model_copy(deep=True)
            for event in self._events.get(calendar_id, [])
            if event.start.date() == day
        ]

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        stored = event.model_copy(deep=True)
        if not stored.etag:
            stored.etag = "v1"
        self._events.setdefault(calendar_id, []).append(stored)
        return stored.model_copy(deep=True)

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        items = self._events.setdefault(calendar_id, [])
        for index, existing in enumerate(items):
            if existing.event_id == event.event_id:
                stored = event.model_copy(deep=True)
                self._version += 1
                stored.etag = f"v{self._version}"
                items[index] = stored
                return stored.model_copy(deep=True)
        raise KeyError(event.event_id)

    async def delete(self, calendar_id: str, event_id: str) -> None:
        items = self._events.setdefault(calendar_id, [])
        self._events[calendar_id] = [e for e in items if e.event_id != event_id]

    def mutate(self, event_id: str) -> None:
        """Simulate an edit made elsewhere — bumps the etag only."""
        for items in self._events.values():
            for event in items:
                if event.event_id == event_id:
                    self._version += 1
                    event.etag = f"v{self._version}"
                    return
        raise KeyError(event_id)


__all__ = ["FakeCalendar"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_calendar_port.py -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Commit**

```bash
git add src/tmbx/calendar tests/unit/tmbx/test_calendar_port.py
git commit -m "feat(tmbx): calendar port, snapshot tokens, drift detection, fake"
```

---

### Task 14: Plan service — read, apply, commit, undo

**Files:**
- Create: `src/tmbx/service.py`
- Test: `tests/unit/tmbx/test_service.py`

**Interfaces:**
- Consumes: `Plan`/`Block` (Task 9), `Patch`/`apply_ops`/`validate_patch` (Task 10), `render_plan` (Task 12), `CalendarPort`/`Snapshot`/`make_snapshot`/`drift` (Task 13), `JournalStore` (Task 2)
- Produces: `PlanService(calendar, store, mint_uid)` with:
  - `async read(calendar_id, day) -> tuple[Plan, Snapshot]`
  - `async apply(snapshot, patch) -> ApplyResult(plan, rendered, violations, overspecified)`
  - `async commit(snapshot, patch, expect="clean") -> CommitResult(tx_id, conflicts, committed)`
  - `async undo(tx_id) -> CommitResult`
- `ConflictError` carrying the drifted event ids

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_service.py
from __future__ import annotations

import itertools
from datetime import date, datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent
from tmbx.core.models import ET
from tmbx.core.ops import Patch, UpdateBlock
from tmbx.journal.store import JournalStore, init_journal
from tmbx.service import ConflictError, PlanService

DAY = date(2026, 8, 17)


def _event(eid, h, start_h, end_h, uid=None):
    return CalendarEvent(
        event_id=eid,
        summary=f"Block {h}",
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        uid=uid or f"u-{eid}",
        handle=h,
    )


@pytest.fixture
async def service(tmp_path):
    calendar = FakeCalendar({"primary": [_event("e1", "PR1", 9, 10), _event("e2", "DW1", 10, 12)]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    svc = PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")
    svc.calendar = calendar
    return svc


async def test_read_returns_plan_and_snapshot(service):
    plan, snapshot = await service.read("primary", DAY)
    assert [b.h for b in plan.blocks] == ["PR1", "DW1"]
    assert snapshot.token


async def test_apply_is_pure_and_writes_nothing_to_the_calendar(service):
    _, snapshot = await service.read("primary", DAY)
    result = await service.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert result.plan.by_handle("DW1").n == "Renamed"
    live = await service.calendar.list_day("primary", DAY)
    assert all(e.summary != "Renamed" for e in live)


async def test_apply_journals_an_attempt(service):
    _, snapshot = await service.read("primary", DAY)
    await service.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    rows = await service.store.by_day("primary", DAY)
    assert len(rows) == 1


async def test_commit_writes_to_the_calendar_and_returns_a_tx_id(service):
    _, snapshot = await service.read("primary", DAY)
    result = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert result.committed and result.tx_id
    live = await service.calendar.list_day("primary", DAY)
    assert any(e.summary == "Renamed" for e in live)


async def test_commit_refuses_when_the_calendar_drifted(service):
    _, snapshot = await service.read("primary", DAY)
    service.calendar.mutate("e1")
    with pytest.raises(ConflictError) as excinfo:
        await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert "e1" in excinfo.value.conflicts


async def test_commit_forces_when_asked(service):
    _, snapshot = await service.read("primary", DAY)
    service.calendar.mutate("e1")
    result = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]), expect="force"
    )
    assert result.committed


async def test_undo_restores_and_is_itself_journaled(service):
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    await service.undo(committed.tx_id)

    live = await service.calendar.list_day("primary", DAY)
    assert any(e.summary == "Block DW1" for e in live)

    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].undoes_tx == committed.tx_id


async def test_undo_refuses_to_clobber_a_newer_edit(service):
    """The failure the legacy undo has: restoring over an edit made since."""
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    service.calendar.mutate("e2")
    with pytest.raises(ConflictError):
        await service.undo(committed.tx_id)


async def test_undo_survives_a_restart(service):
    """Undo state lives in the journal, not process memory — #112.

    A fresh service sharing only the calendar and the store must still undo.
    """
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))

    restarted = PlanService(service.calendar, service.store, mint_uid=lambda: "u-x")
    await restarted.undo(committed.tx_id)

    live = await service.calendar.list_day("primary", DAY)
    assert any(e.summary == "Block DW1" for e in live)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.service'`

- [ ] **Step 3: Write the service**

```python
# src/tmbx/service.py
"""Plan service — the mechanism behind every MCP tool.

Stateless with respect to sessions: every call is keyed by
``(calendar_id, day)``. Durable state lives in the calendar (the plan) and
the journal (the history).
"""

from __future__ import annotations

import json
import uuid
from datetime import date as date_type
from datetime import datetime
from typing import Callable, Literal

from pydantic import BaseModel

from .calendar.port import CalendarEvent, CalendarPort, Snapshot, drift, make_snapshot
from .core.commitment import overspecified
from .core.models import ET, Block, FixedWindow, Plan
from .core.ops import Patch, apply_ops
from .core.render import render_plan
from .journal.models import EntryKind, JournalEntry, PatchOutcome
from .journal.store import JournalStore


class ConflictError(RuntimeError):
    """A write was refused because the calendar changed since the snapshot."""

    def __init__(self, conflicts: list[str]) -> None:
        super().__init__(f"calendar drifted: {', '.join(conflicts)}")
        self.conflicts = conflicts


class ApplyResult(BaseModel):
    plan: Plan
    rendered: str
    violations: list[str]
    overspecified: list[str]


class CommitResult(BaseModel):
    tx_id: str | None
    committed: bool
    conflicts: list[str] = []


def _event_to_block(event: CalendarEvent, index: int) -> Block:
    """Build a block from a calendar event, minting a handle if absent."""
    handle = event.handle or f"EVT{index + 1}"
    return Block(
        uid=event.uid or f"u-{event.event_id}",
        h=handle,
        slug=event.slug,
        n=event.summary,
        d=event.description,
        t=ET.M,
        p=FixedWindow(st=event.start.time(), et=event.end.time()),
        anchor_source="calendar",
    )


class PlanService:
    """Read, preview, commit and undo day plans."""

    def __init__(
        self,
        calendar: CalendarPort,
        store: JournalStore,
        *,
        mint_uid: Callable[[], str] | None = None,
    ) -> None:
        self.calendar = calendar
        self.store = store
        self._mint_uid = mint_uid or (lambda: uuid.uuid4().hex)
        # Snapshot cache only. Undo state lives in the journal, not here — a
        # process-local transaction dict is the defect behind #112.
        self._snapshots: dict[str, Snapshot] = {}
        self._plans: dict[str, Plan] = {}

    async def read(
        self, calendar_id: str, day: date_type
    ) -> tuple[Plan, Snapshot]:
        """Fetch live calendar state as a plan plus a snapshot token."""
        events = await self.calendar.list_day(calendar_id, day)
        events.sort(key=lambda e: e.start)
        plan = Plan(
            date=day,
            blocks=[_event_to_block(event, index) for index, event in enumerate(events)],
        )
        snapshot = make_snapshot(calendar_id, day, events)
        self._snapshots[snapshot.token] = snapshot
        self._plans[snapshot.token] = plan
        return plan, snapshot

    def _resolve_snapshot(self, snapshot: Snapshot | str) -> tuple[Snapshot, Plan]:
        token = snapshot if isinstance(snapshot, str) else snapshot.token
        if token not in self._snapshots:
            raise KeyError(f"unknown snapshot token {token}")
        return self._snapshots[token], self._plans[token]

    async def apply(self, snapshot: Snapshot | str, patch: Patch) -> ApplyResult:
        """Pure preview. Applies ops, validates, journals the attempt."""
        snap, plan = self._resolve_snapshot(snapshot)

        violations: list[str] = []
        outcome = PatchOutcome.APPLIED
        try:
            patched = apply_ops(plan, patch, mint_uid=self._mint_uid)
        except ValueError as exc:
            outcome = PatchOutcome.APPLY_FAILED
            await self._journal(snap, patch, outcome, error=str(exc))
            raise

        try:
            patched.resolve()
        except ValueError as exc:
            violations.append(str(exc))
            outcome = PatchOutcome.VALIDATION_FAILED

        await self._journal(snap, patch, outcome)
        return ApplyResult(
            plan=patched,
            rendered=render_plan(patched),
            violations=violations,
            overspecified=overspecified(patched),
        )

    async def commit(
        self,
        snapshot: Snapshot | str,
        patch: Patch,
        *,
        expect: Literal["clean", "force"] = "clean",
    ) -> CommitResult:
        """Check preconditions, write to the calendar, journal the commit."""
        snap, plan = self._resolve_snapshot(snapshot)

        live = await self.calendar.list_day(snap.calendar_id, snap.day)
        conflicts = drift(snap, live)
        if conflicts and expect != "force":
            raise ConflictError(conflicts)

        patched = apply_ops(plan, patch, mint_uid=self._mint_uid)
        before = [event.model_copy(deep=True) for event in live]
        await self._write(snap, patched)

        # Capture state as it stands immediately after the write. Undo compares
        # live state against THIS. Re-deriving it at undo time would compare the
        # calendar against itself, making the conflict check a silent no-op.
        post_write = make_snapshot(
            snap.calendar_id,
            snap.day,
            await self.calendar.list_day(snap.calendar_id, snap.day),
        )

        tx_id = uuid.uuid4().hex
        await self._journal(
            snap,
            patch,
            PatchOutcome.APPLIED,
            kind=EntryKind.COMMIT,
            tx_id=tx_id,
            before=before,
            post_etags=post_write.etags,
        )
        return CommitResult(tx_id=tx_id, committed=True, conflicts=conflicts)

    async def undo(self, tx_id: str) -> CommitResult:
        """Restore the pre-commit state, refusing to clobber newer edits.

        Reads its state from the journal, not from process memory, so undo
        survives a restart — the defect behind #112.
        """
        row = await self.store.by_tx_id(tx_id)
        if row is None or row.before_json is None or row.post_etags_json is None:
            raise KeyError(f"unknown or non-undoable transaction {tx_id}")

        calendar_id, day = row.calendar_id, row.plan_date
        before = [
            CalendarEvent.model_validate(item) for item in json.loads(row.before_json)
        ]
        post_etags: dict[str, str] = json.loads(row.post_etags_json)

        live = await self.calendar.list_day(calendar_id, day)
        live_etags = {event.event_id: event.etag for event in live}
        conflicts = sorted(
            {
                event_id
                for event_id in set(post_etags) | set(live_etags)
                if post_etags.get(event_id) != live_etags.get(event_id)
            }
        )
        if conflicts:
            raise ConflictError(conflicts)

        current_ids = {event.event_id for event in live}
        for event in before:
            if event.event_id in current_ids:
                await self.calendar.update(calendar_id, event)
            else:
                await self.calendar.create(calendar_id, event)
        for event_id in current_ids - {event.event_id for event in before}:
            await self.calendar.delete(calendar_id, event_id)

        undo_tx = uuid.uuid4().hex
        entry = JournalEntry(
            calendar_id=calendar_id,
            plan_date=day,
            kind=EntryKind.UNDO,
            outcome=PatchOutcome.APPLIED,
            tx_id=undo_tx,
            undoes_tx=tx_id,
        )
        await self.store.append(entry)
        return CommitResult(tx_id=undo_tx, committed=True)

    async def _write(self, snap: Snapshot, plan: Plan) -> None:
        """Push a plan to the calendar.

        Provider event ids come from the snapshot's uid→event_id map, never
        from the uid's string form. uid is opaque; deriving one identifier
        from the other silently breaks the moment uids are really minted.
        """
        resolved = {row.h: row for row in plan.resolve(check_overlap=False)}
        existing = {
            event.event_id: event
            for event in await self.calendar.list_day(snap.calendar_id, snap.day)
        }
        keep: set[str] = set()

        for block in plan.blocks:
            row = resolved[block.h]
            event_id = snap.event_ids.get(block.uid) or f"tmbx{uuid.uuid4().hex[:20]}"
            keep.add(event_id)
            event = CalendarEvent(
                event_id=event_id,
                summary=block.n,
                description=block.d,
                start=datetime.combine(plan.date, row.start),
                end=datetime.combine(plan.date, row.end),
                uid=block.uid,
                handle=block.h,
                slug=block.slug,
            )
            if event_id in existing:
                await self.calendar.update(snap.calendar_id, event)
            else:
                await self.calendar.create(snap.calendar_id, event)

        for event_id in set(existing) - keep:
            await self.calendar.delete(snap.calendar_id, event_id)

    async def _journal(
        self,
        snap: Snapshot,
        patch: Patch,
        outcome: PatchOutcome,
        *,
        kind: EntryKind = EntryKind.ATTEMPT,
        tx_id: str | None = None,
        error: str | None = None,
        before: list[CalendarEvent] | None = None,
        post_etags: dict[str, str] | None = None,
    ) -> None:
        entry = JournalEntry(
            calendar_id=snap.calendar_id,
            plan_date=snap.day,
            kind=kind,
            ops_json=patch.model_dump_json(),
            outcome=outcome,
            error=error,
            tx_id=tx_id,
            before_json=(
                json.dumps([event.model_dump(mode="json") for event in before])
                if before is not None
                else None
            ),
            post_etags_json=(
                json.dumps(post_etags) if post_etags is not None else None
            ),
        )
        await self.store.append(entry)


__all__ = ["ApplyResult", "CommitResult", "ConflictError", "PlanService"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_service.py -v`
Expected: PASS, 8 tests. The two conflict tests are the ones that matter — they encode the failures the legacy engine has.

- [ ] **Step 5: Commit**

```bash
git add src/tmbx/service.py tests/unit/tmbx/test_service.py
git commit -m "feat(tmbx): plan service with snapshot preconditions on commit and undo"
```

---

### Task 15: MCP server

**Files:**
- Create: `src/tmbx/server.py`
- Test: `tests/unit/tmbx/test_server.py`

**Interfaces:**
- Consumes: `PlanService`, `ConflictError` (Task 14); `Patch` (Task 10); `init_journal`, `JournalStore` (Task 2)
- Produces: `build_server(service) -> FastMCP` exposing `plan_read`, `plan_apply`, `plan_commit`, `plan_undo`, `plan_history`, plus resources `tmbx://schema/ops` and `tmbx://policy/planning`. `main()` is the stdio entrypoint.

`patch_nl` is deliberately absent at level 1 — Claude Code emits `Patch` directly against the published schema. It arrives with the Slack host.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/tmbx/test_server.py
from __future__ import annotations

import itertools
import json
from datetime import date, datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent
from tmbx.journal.store import JournalStore, init_journal
from tmbx.server import build_server
from tmbx.service import PlanService


@pytest.fixture
async def server(tmp_path):
    calendar = FakeCalendar(
        {
            "primary": [
                CalendarEvent(
                    event_id="e1",
                    summary="Plan",
                    start=datetime(2026, 8, 17, 9, 0),
                    end=datetime(2026, 8, 17, 9, 30),
                    etag="v1",
                    uid="u-e1",
                    handle="PR1",
                )
            ]
        }
    )
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    service = PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")
    return build_server(service)


async def test_exposes_exactly_the_level_one_tools(server):
    names = {tool.name for tool in await server.list_tools()}
    assert names == {"plan_read", "plan_apply", "plan_commit", "plan_undo", "plan_history"}


async def test_patch_nl_is_absent_at_level_one(server):
    names = {tool.name for tool in await server.list_tools()}
    assert "patch_nl" not in names


async def test_exposes_the_op_schema_resource(server):
    uris = {str(resource.uri) for resource in await server.list_resources()}
    assert "tmbx://schema/ops" in uris


async def test_plan_read_returns_a_rendered_plan_and_token(server):
    result = await server.call_tool("plan_read", {"calendar_id": "primary", "day": "2026-08-17"})
    payload = json.loads(result[0].text if hasattr(result[0], "text") else result[1])
    assert "PR1" in payload["rendered"]
    assert payload["snapshot"]


async def test_conflict_is_reported_as_refusal_not_crash(server):
    read = await server.call_tool("plan_read", {"calendar_id": "primary", "day": "2026-08-17"})
    payload = json.loads(read[0].text if hasattr(read[0], "text") else read[1])
    server_service = server.tmbx_service
    server_service.calendar.mutate("e1")

    result = await server.call_tool(
        "plan_commit",
        {
            "snapshot": payload["snapshot"],
            "patch": {"ops": [{"op": "update", "h": "PR1", "n": "Renamed"}]},
        },
    )
    body = json.loads(result[0].text if hasattr(result[0], "text") else result[1])
    assert body["committed"] is False
    assert body["conflicts"] == ["e1"]
    assert "stale" in body["message"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/tmbx/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tmbx.server'`

- [ ] **Step 3: Write the server**

```python
# src/tmbx/server.py
"""MCP server exposing the level 1 timebox tools.

Six tools were designed; five ship at level 1. ``patch_nl`` is deliberately
absent — when the host is an LLM, putting an LLM inside the tool means a
weaker model re-deriving intent the host already understood, without the
host's context. Claude Code emits ``Patch`` directly. ``patch_nl`` arrives
with the Slack host, which is not an LLM.

Reference material is exposed as resources, not tools, so the tool count and
the tool descriptions both stay small.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core.ops import Patch
from .core.render import render_plan
from .journal.disposition import derive_dispositions
from .service import ConflictError, PlanService

PLANNING_POLICY = """\
Use the weakest timing mode that expresses the intent.

- ap: duration only, starts when the previous block ends. The default.
- bn: duration only, ends when the next block starts.
- fs: pinned start plus duration. Requires anchor_source.
- fw: pinned start and end; duration is inferred. Requires anchor_source.

Never pin a block because it is convenient. A fixed time exists because the
user stated one, a constraint requires it, or it came off the calendar —
record which in anchor_source. Every gratuitous pin stops the chain from
absorbing later edits.

Address blocks by handle. Handles are shown in the rendered plan. A patch is
a set: every op resolves against the plan as rendered, so op order does not
matter and no op may reference a block created by another op.
"""


def build_server(service: PlanService) -> FastMCP:
    """Build the MCP server around a plan service."""
    mcp = FastMCP(
        name="tmbx",
        instructions=(
            "Read a day's plan, preview typed patches against it, commit them, "
            "and undo. Always plan_read first — every write needs a snapshot token."
        ),
    )
    mcp.tmbx_service = service  # type: ignore[attr-defined]

    @mcp.tool(name="plan_read")
    async def plan_read(calendar_id: str, day: str) -> str:
        """Fetch a day's plan. Returns the rendered plan and a snapshot token.

        The token is required by plan_apply and plan_commit and pins the
        calendar state you saw.
        """
        plan, snapshot = await service.read(calendar_id, date_type.fromisoformat(day))
        return json.dumps(
            {
                "snapshot": snapshot.token,
                "rendered": render_plan(plan),
                "blocks": len(plan.blocks),
            }
        )

    @mcp.tool(name="plan_apply")
    async def plan_apply(snapshot: str, patch: dict[str, Any]) -> str:
        """Preview a patch. Writes nothing. Returns the resulting plan.

        ``overspecified`` lists handles whose fixed timing could be relaxed to
        ap with no change to any time — treat those as mistakes.
        """
        try:
            result = await service.apply(snapshot, Patch.model_validate(patch))
        except ValueError as exc:
            return json.dumps({"ok": False, "message": str(exc)})
        return json.dumps(
            {
                "ok": True,
                "rendered": result.rendered,
                "violations": result.violations,
                "overspecified": result.overspecified,
            }
        )

    @mcp.tool(name="plan_commit")
    async def plan_commit(
        snapshot: str, patch: dict[str, Any], expect: str = "clean"
    ) -> str:
        """Write a patch to the calendar.

        Refuses if the calendar changed since the snapshot. A refusal is not a
        transient error — do not retry it. Call plan_read again to see the new
        state, then re-derive the patch. Pass expect="force" only when the user
        has said to overwrite.
        """
        try:
            result = await service.commit(
                snapshot, Patch.model_validate(patch), expect=expect  # type: ignore[arg-type]
            )
        except ConflictError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "conflicts": exc.conflicts,
                    "message": (
                        "Your snapshot is stale — the calendar changed. "
                        "Re-read the plan and rebuild the patch."
                    ),
                }
            )
        except ValueError as exc:
            return json.dumps({"committed": False, "conflicts": [], "message": str(exc)})
        return json.dumps({"committed": True, "tx_id": result.tx_id, "conflicts": []})

    @mcp.tool(name="plan_undo")
    async def plan_undo(tx_id: str) -> str:
        """Reverse a committed transaction.

        Refuses if the calendar changed since that commit, rather than
        restoring over a newer edit.
        """
        try:
            result = await service.undo(tx_id)
        except ConflictError as exc:
            return json.dumps(
                {
                    "committed": False,
                    "conflicts": exc.conflicts,
                    "message": "Refusing to undo — it would overwrite a newer edit.",
                }
            )
        except KeyError as exc:
            return json.dumps({"committed": False, "message": str(exc)})
        return json.dumps({"committed": True, "tx_id": result.tx_id})

    @mcp.tool(name="plan_history")
    async def plan_history(calendar_id: str, day: str) -> str:
        """List patch attempts, commits and undos for a day, with dispositions."""
        entries = await service.store.by_day(calendar_id, date_type.fromisoformat(day))
        dispositions = derive_dispositions(entries)
        return json.dumps(
            [
                {
                    "id": entry.id,
                    "kind": entry.kind.value,
                    "outcome": entry.outcome.value,
                    "disposition": dispositions[entry.id].value,
                    "tx_id": entry.tx_id,
                }
                for entry in entries
                if entry.id is not None
            ]
        )

    @mcp.resource("tmbx://schema/ops")
    def ops_schema() -> str:
        """JSON schema for the Patch object accepted by plan_apply/plan_commit."""
        return json.dumps(Patch.model_json_schema(), indent=2)

    @mcp.resource("tmbx://policy/planning")
    def planning_policy() -> str:
        """Timing grammar and least-commitment policy."""
        return PLANNING_POLICY

    return mcp


def main() -> None:
    """stdio entrypoint."""
    import asyncio

    from .calendar.fake import FakeCalendar
    from .journal.store import JournalStore, init_journal

    async def _build() -> FastMCP:
        store = JournalStore(await init_journal())
        return build_server(PlanService(FakeCalendar(), store))

    asyncio.run(_build()).run()


__all__ = ["PLANNING_POLICY", "build_server", "main"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/unit/tmbx/test_server.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Register the entrypoint**

In `pyproject.toml`, under `[project.scripts]`, add alongside the existing entries:

```toml
tmbx-mcp = "tmbx.server:main"
```

- [ ] **Step 6: Run the whole suite**

Run: `poetry run pytest tests/unit/tmbx -v`
Expected: PASS — all of Parts A and B.

- [ ] **Step 7: Commit**

```bash
git add src/tmbx/server.py tests/unit/tmbx/test_server.py pyproject.toml
git commit -m "feat(tmbx): MCP server with five level-1 tools and schema resources"
```

---

## Done

At this point:

- The journal is filling from the legacy agent, with constraint provenance and derived dispositions, and the constraint memory map has a read API.
- The level 1 vocabulary exists, is commutative under set semantics, addresses by handle, and refuses to clobber concurrent edits on both commit and undo.
- Claude Code can drive it over MCP.

**Deliberately not built here, and why:**

- **`patch_nl` and the DSPy artifact seam.** The spec puts DSPy in as a build-time compiler loading a prompt artifact at runtime. Its only target is `patch_nl`, which does not exist at level 1 because Claude Code emits `Patch` directly. Building an artifact-loading seam with nothing to load would be scaffolding for its own sake. Both arrive together with the Slack host.
- **The real Google Calendar adapter.** `CalendarPort` and `FakeCalendar` define the contract; the Google implementation is mechanical once `plan_read`/`plan_commit` are proven against the fake, and none of the level-1 questions need it.

**Next, outside this plan:** the `tmbx` REPL (#125 decides whether to harvest the existing branch), the Google Calendar adapter behind `CalendarPort`, and the level 2 vocabulary (#147) — whose op set should be chosen from what level 1 could not express, not from this document.

**The measurement that gates level 2.** Task 10's commutativity test proves the ops compose; it does not prove the model can *produce* them. Once the server runs, plan real days through it and keep a list of instructions that could not be expressed with four ops. That list is #147's input. Without it, level 2 gets designed from taste — which is the failure this ladder exists to avoid.
