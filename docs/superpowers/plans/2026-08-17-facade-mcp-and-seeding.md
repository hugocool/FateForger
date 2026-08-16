# Facade, MCP Binding, and Corpus Seeding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the memory server demonstrable in the first real timeboxing session — one entry point for a session, an MCP binding, and a store seeded from the user's real legacy preferences.

**Architecture:** A `MemoryService` facade owns both stores and a judge, and exposes the two verbs a session needs: `observe` (ingest → project) and `get_active_constraints` (delegation, still no model in the read path). The MCP binding is a thin FastMCP wrapper over the facade, built by a factory so tests inject a stub judge. Seeding replays the 97 legacy PROFILE rows through the **real pipeline** — the canonicalise judgement does the dedup, because that is the only permitted way to decide two rows mean the same thing.

**Tech Stack:** Python 3.11, pydantic v2, `mcp` (already a project dependency — `FastMCP` is used by `scripts/constraint_mcp_server.py`), stdlib `sqlite3`, pytest 8.

**Builds on:** `2026-08-16-constraint-layer-and-read-api.md`, complete at `ccfc50c` — 61 unit tests, 8 eval tests.

**Spec:** `docs/superpowers/specs/2026-08-16-kg-memory-server-design.md`

**Serves:** #149 (session one), #133's destination ("bound two ways: as an MCP server, and directly in-process" — only in-process exists today).

## Spec conformance, checked before execution

| | |
|---|---|
| **I1** — no model in the read path | The facade's `get_active_constraints` delegates to `read_api`; the MCP read tool calls only that. The read tool is sync. ✅ |
| **I2** — L1 append-only | The facade writes only via `ingest`; seeding writes only via the facade. ✅ |
| **I3** — identity minted | Seeding never carries legacy ids or `hints["uid"]` into the new store; every observation and constraint gets a fresh minted uid. Legacy identity was content-derived in 97.5% of rows and must not be imported. ✅ |
| **I5** — serialisation | `observe` runs ingest then project; project already holds the per-store lock. The backfill replays rows **sequentially** — deliberate, because each row's canonicalise depends on the constraints created by prior rows. ✅ |
| **No pattern matching** | Seeding does **zero** dedup of its own. 97 rows go in; `dedup` (within-thread) and `canonicalise` (cross-thread) collapse them. The pipeline is the dedup. ✅ |

## What already exists

- `src/memory/` — `ObservationStore`, `ConstraintStore`, `Judge`/`StubJudge`, `OpenRouterJudge`, `ingest`, `project`, `get_active_constraints`, models and enums. 61 unit tests.
- `scripts/constraint_mcp_server.py` — the repo's FastMCP idiom (`FastMCP(name=...)`, `@mcp.tool(name=...)`).
- `data/admonish.db` — legacy store; 97 PROFILE rows across 23 threads, 2 rows with no `thread_ts`.

## File Structure

| File | Responsibility |
|---|---|
| `src/memory/service.py` | `MemoryService` facade + `ObserveOutcome` |
| `src/memory/openrouter_judge.py` | add `openrouter_judge_from_env()` |
| `src/memory/mcp_server.py` | `build_server(service)` factory + stdio `__main__` |
| `src/memory/backfill.py` | legacy replay + `BackfillReport`, `__main__` |
| `tests/memory/` | one test module per new source module |

---

### Task 1: The `MemoryService` facade

**Files:**
- Create: `src/memory/service.py`
- Modify: `src/memory/openrouter_judge.py` (add `openrouter_judge_from_env`)
- Modify: `src/memory/__init__.py` (export `MemoryService`, `ObserveOutcome`)
- Test: `tests/memory/test_service.py`

**Interfaces:**
- Consumes: everything from the previous plans.
- Produces: `ObserveOutcome` (`stored: bool`, `suppressed_as: str | None`, `constraint_uid: str | None`, `constraint_name: str | None`, `tier: Tier | None`); `MemoryService(db_path: str, judge: Judge)` with `async observe(text, channel, session_id, observed_at, provenance=Provenance.OBSERVED) -> ObserveOutcome` and `get_active_constraints(day: date, stage: str | None = None) -> list[ConstraintView]`; `openrouter_judge_from_env() -> OpenRouterJudge`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_service.py
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from memory.judge import StubJudge
from memory.models import Channel, Provenance, Tier
from memory.service import MemoryService, ObserveOutcome

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
MONDAY = date(2026, 3, 9)


def _service(tmp_path, judge=None) -> MemoryService:
    return MemoryService(str(tmp_path / "memory.db"), judge or StubJudge())


async def test_observe_stores_projects_and_reports(tmp_path):
    judge = StubJudge(
        tiers={"eat oats two hours before gym": Tier.DURABLE},
        labels={"eat oats two hours before gym": "Oats before gym"},
    )
    service = _service(tmp_path, judge)
    outcome = await service.observe(
        "eat oats two hours before gym",
        channel=Channel.PLANNING,
        session_id="s1",
        observed_at=T0,
    )
    assert outcome.stored is True
    assert outcome.constraint_name == "Oats before gym"
    assert outcome.tier is Tier.DURABLE
    views = service.get_active_constraints(MONDAY)
    assert [v.name for v in views] == ["Oats before gym"]
    assert views[0].uid == outcome.constraint_uid


async def test_a_suppressed_observation_projects_nothing(tmp_path):
    judge = StubJudge(metas={"begin the timeboxing session": True})
    service = _service(tmp_path, judge)
    outcome = await service.observe(
        "begin the timeboxing session",
        channel=Channel.PLANNING,
        session_id="s1",
        observed_at=T0,
    )
    assert outcome.stored is False
    assert outcome.suppressed_as == "meta"
    assert outcome.constraint_uid is None
    assert service.get_active_constraints(MONDAY) == []


async def test_a_session_fact_is_stored_but_not_served(tmp_path):
    service = _service(tmp_path)  # stub default tier is SESSION
    outcome = await service.observe(
        "hockey at 11:45 today",
        channel=Channel.PLANNING,
        session_id="s1",
        observed_at=T0,
    )
    assert outcome.stored is True
    assert outcome.tier is Tier.SESSION
    assert service.get_active_constraints(MONDAY) == []


async def test_generated_provenance_is_rejected_without_judging(tmp_path):
    judge = StubJudge()
    service = _service(tmp_path, judge)
    outcome = await service.observe(
        "pre-gym oats",
        channel=Channel.CALENDAR,
        session_id="s1",
        observed_at=T0,
        provenance=Provenance.GENERATED,
    )
    assert outcome.stored is False
    assert outcome.suppressed_as == "generated"
    assert judge.calls == []


async def test_a_restatement_folds_rather_than_duplicating(tmp_path):
    judge = StubJudge(
        tiers={
            "oats two hours before gym": Tier.DURABLE,
            "I need oats 2h ahead of the gym": Tier.DURABLE,
        },
        labels={"oats two hours before gym": "Oats before gym"},
    )
    service = _service(tmp_path, judge)
    first = await service.observe(
        "oats two hours before gym",
        channel=Channel.PLANNING, session_id="s1", observed_at=T0,
    )
    judge._canonical["I need oats 2h ahead of the gym"] = first.constraint_uid
    second = await service.observe(
        "I need oats 2h ahead of the gym",
        channel=Channel.PLANNING, session_id="s2", observed_at=T0,
    )
    assert second.constraint_uid == first.constraint_uid
    assert len(service.get_active_constraints(MONDAY)) == 1


def test_one_db_file_carries_both_stores(tmp_path):
    service = _service(tmp_path)
    assert (tmp_path / "memory.db").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.service'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/service.py
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from memory.constraint import ConstraintView
from memory.constraint_store import ConstraintStore
from memory.ingest import ingest
from memory.judge import Judge
from memory.models import Channel, Observation, Provenance, Tier
from memory.projection import project
from memory.read_api import get_active_constraints as _read
from memory.store import ObservationStore


class ObserveOutcome(BaseModel):
    """What happened to one statement, in terms a host can display."""

    stored: bool
    suppressed_as: str | None = None
    constraint_uid: str | None = None
    constraint_name: str | None = None
    tier: Tier | None = None


class MemoryService:
    """One session-shaped entry point over the whole pipeline.

    Owns both stores on a single sqlite file (the schemas do not collide) and
    a judge. `observe` is the write path: ingest, then — only if the
    observation was stored — projection into a constraint. The read path
    delegates to `read_api` and stays synchronous: no judge, no await.
    """

    def __init__(self, db_path: str, judge: Judge) -> None:
        self._observations = ObservationStore(db_path)
        self._constraints = ConstraintStore(db_path)
        self._judge = judge

    async def observe(
        self,
        text: str,
        *,
        channel: Channel,
        session_id: str | None,
        observed_at: datetime,
        provenance: Provenance = Provenance.OBSERVED,
    ) -> ObserveOutcome:
        observation = Observation(
            text=text,
            channel=channel,
            provenance=provenance,
            session_id=session_id,
            observed_at=observed_at,
        )
        result = await ingest(observation, self._judge, self._observations)
        if not result.stored:
            return ObserveOutcome(stored=False, suppressed_as=result.suppressed_as)
        constraint = await project(
            observation, result, self._judge, self._constraints
        )
        return ObserveOutcome(
            stored=True,
            constraint_uid=constraint.uid,
            constraint_name=constraint.name,
            tier=constraint.tier,
        )

    def get_active_constraints(
        self, day: date, stage: str | None = None
    ) -> list[ConstraintView]:
        return _read(self._constraints, day, stage)
```

Add to `src/memory/openrouter_judge.py`:

```python
import os


def openrouter_judge_from_env() -> OpenRouterJudge:
    """Build the real judge from environment configuration.

    Raises rather than defaulting when the key is absent: a memory server
    that silently cannot judge is worse than one that refuses to start.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return OpenRouterJudge(
        api_key=api_key,
        base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ),
    )
```

Export `MemoryService`, `ObserveOutcome`, `openrouter_judge_from_env` from `src/memory/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 67 passed (6 new + 61 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/service.py src/memory/openrouter_judge.py src/memory/__init__.py tests/memory/test_service.py
git commit -m "feat(memory): MemoryService facade — one session-shaped entry point"
```

---

### Task 2: The MCP binding

**Files:**
- Create: `src/memory/mcp_server.py`
- Test: `tests/memory/test_mcp_server.py`

**Interfaces:**
- Consumes: `MemoryService`, `openrouter_judge_from_env`.
- Produces: `build_server(service: MemoryService) -> FastMCP`; running `python -m memory.mcp_server` starts a stdio server on a real judge and a `MEMORY_DB_PATH` env var (default `data/memory.db`).

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_mcp_server.py
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from memory.judge import StubJudge
from memory.mcp_server import build_server
from memory.models import Tier
from memory.service import MemoryService

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _service(tmp_path, judge=None) -> MemoryService:
    return MemoryService(str(tmp_path / "memory.db"), judge or StubJudge())


async def test_the_server_exposes_exactly_the_session_verbs(tmp_path):
    server = build_server(_service(tmp_path))
    tools = {t.name for t in await server.list_tools()}
    assert tools == {"memory_observe", "memory_get_active_constraints"}


async def test_observe_tool_round_trips(tmp_path):
    judge = StubJudge(
        tiers={"no meetings before 13:00": Tier.DURABLE},
        labels={"no meetings before 13:00": "No morning meetings"},
        declarations={"no meetings before 13:00": True},
    )
    server = build_server(_service(tmp_path, judge))
    result = await server.call_tool(
        "memory_observe",
        {"text": "no meetings before 13:00", "session_id": "s1"},
    )
    payload = json.loads(result[0].text)
    assert payload["stored"] is True
    assert payload["constraint_name"] == "No morning meetings"


async def test_read_tool_serves_what_observe_stored(tmp_path):
    judge = StubJudge(
        tiers={"no meetings before 13:00": Tier.DURABLE},
        labels={"no meetings before 13:00": "No morning meetings"},
    )
    server = build_server(_service(tmp_path, judge))
    await server.call_tool(
        "memory_observe",
        {"text": "no meetings before 13:00", "session_id": "s1"},
    )
    result = await server.call_tool(
        "memory_get_active_constraints", {"day": "2026-03-09"}
    )
    rows = json.loads(result[0].text)
    assert len(rows) == 1
    assert rows[0]["name"] == "No morning meetings"
    assert rows[0]["necessity"] == "should"
    assert "uid" in rows[0]


async def test_the_read_tool_is_not_async_in_the_service(tmp_path):
    """I1 holds through the binding: the read path stays synchronous."""
    import inspect

    from memory.service import MemoryService as S

    assert not inspect.iscoroutinefunction(S.get_active_constraints)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.mcp_server'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/mcp_server.py
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from mcp.server.fastmcp import FastMCP

from memory.models import Channel
from memory.service import MemoryService

INSTRUCTIONS = """\
Durable memory for a personal scheduling agent.

memory_observe records something the user said; the server decides what it
means (which anchors, durable rule or today's fact, interaction chatter,
restatement of a known rule) and files it. memory_get_active_constraints
returns the durable rules applying on a day — structural filtering only, so
expect every applicable rule rather than a semantically ranked subset.\
"""


def build_server(service: MemoryService) -> FastMCP:
    """The MCP face of a MemoryService.

    A factory rather than module state so tests bind a stub judge and a tmp
    database, and so a host can run several isolated servers.
    """
    mcp = FastMCP(name="memory", instructions=INSTRUCTIONS)

    @mcp.tool(name="memory_observe")
    async def memory_observe(
        text: str,
        session_id: str,
        channel: str = "planning",
        observed_at: str | None = None,
    ) -> dict:
        """Record one user statement and file it into memory."""
        when = (
            datetime.fromisoformat(observed_at)
            if observed_at
            else datetime.now(timezone.utc)
        )
        outcome = await service.observe(
            text,
            channel=Channel(channel),
            session_id=session_id,
            observed_at=when,
        )
        return outcome.model_dump(mode="json")

    @mcp.tool(name="memory_get_active_constraints")
    def memory_get_active_constraints(
        day: str, stage: str | None = None
    ) -> list[dict]:
        """Durable rules applying on `day` (YYYY-MM-DD). No model call."""
        views = service.get_active_constraints(date.fromisoformat(day), stage)
        return [v.model_dump(mode="json") for v in views]

    return mcp


def main() -> None:
    from memory.openrouter_judge import openrouter_judge_from_env

    db_path = os.environ.get("MEMORY_DB_PATH", "data/memory.db")
    service = MemoryService(db_path, openrouter_judge_from_env())
    build_server(service).run()  # stdio transport


if __name__ == "__main__":
    main()
```

Note for the implementer: `FastMCP.call_tool` returns a list of content blocks whose `.text` is JSON when the tool returns a dict/list — if the installed `mcp` version wraps differently, adapt the *tests'* unwrapping, not the tool signatures, and record what you found in your report.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 71 passed (4 new + 67 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/mcp_server.py tests/memory/test_mcp_server.py
git commit -m "feat(memory): MCP binding — memory_observe and memory_get_active_constraints"
```

---

### Task 3: The backfill module

**Files:**
- Create: `src/memory/backfill.py`
- Test: `tests/memory/test_backfill.py`

**Interfaces:**
- Consumes: `MemoryService`.
- Produces: `LegacyRow` (`name`, `description`, `thread_ts`, `created_at`); `read_profile_rows(legacy_db_path: str) -> list[LegacyRow]`; `BackfillReport` (`rows_read`, `stored`, `suppressed: dict[str, int]`, `constraints_created`, `folds`, `durable`, `outcomes: list[ObserveOutcome]`); `async backfill(legacy_db_path, service) -> BackfillReport`; `python -m memory.backfill <legacy_db> <memory_db>` runs it on the real judge and prints the report.

**Design notes the implementer must understand:**

1. **The pipeline is the dedup.** This module does no dedup, no normalisation, no comparison of any kind between rows. 97 rows go in; the `dedup` judgement collapses within-thread machine duplication and `canonicalise` folds cross-thread restatements. If you find yourself comparing two rows' text, stop — that is the banned judgement.
2. **Legacy identity does not cross.** No legacy id, `hints["uid"]`, or content-derived key enters the new store. Every observation is minted fresh (I3).
3. **Replay is sequential**, row by row in `created_at` order — each row's canonicalise depends on the constraints prior rows created. Do not `gather` the rows.
4. The observation text is `f"{name}: {description}"` when both exist, else whichever is non-empty. That is serialisation for the judge to read, not a judgement.
5. `session_id` is `thread_ts` (system-minted); rows with no `thread_ts` get `"legacy:no-thread"`.
6. A judge failure on one row **stops the run** (fail loudly, partial report printed). No retry loop, no skip-and-continue — a half-poisoned store discovered later is worse than a stopped backfill.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_backfill.py
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from memory.backfill import BackfillReport, backfill, read_profile_rows
from memory.judge import StubJudge
from memory.models import Tier
from memory.service import MemoryService

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _legacy_db(tmp_path, rows) -> str:
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE timeboxing_constraints ("
        " id INTEGER PRIMARY KEY, name TEXT, description TEXT,"
        " scope TEXT, thread_ts TEXT, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO timeboxing_constraints"
        " (name, description, scope, thread_ts, created_at) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return path


def test_read_profile_rows_reads_profile_only_in_created_order(tmp_path):
    path = _legacy_db(
        tmp_path,
        [
            ("Late", "later row", "PROFILE", "t1", "2026-03-02T10:00:00"),
            ("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T10:00:00"),
            ("Hockey", "today only", "SESSION", "t2", "2026-03-01T11:00:00"),
        ],
    )
    rows = read_profile_rows(path)
    assert [r.name for r in rows] == ["Sleep", "Late"]


async def test_backfill_replays_through_the_real_pipeline(tmp_path):
    path = _legacy_db(
        tmp_path,
        [("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T10:00:00")],
    )
    judge = StubJudge(
        tiers={"Sleep: sleep at 23:00": Tier.DURABLE},
        labels={"Sleep: sleep at 23:00": "Sleep at 23:00"},
    )
    service = MemoryService(str(tmp_path / "memory.db"), judge)
    report = await backfill(path, service)
    assert report.rows_read == 1
    assert report.stored == 1
    assert report.constraints_created == 1
    assert report.durable == 1
    views = service.get_active_constraints(date(2026, 3, 9))
    assert [v.name for v in views] == ["Sleep at 23:00"]


async def test_suppressed_rows_are_counted_not_projected(tmp_path):
    path = _legacy_db(
        tmp_path,
        [
            ("Meta", "begin the timeboxing session", "PROFILE", "t1", "2026-03-01T10:00:00"),
            ("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T11:00:00"),
        ],
    )
    judge = StubJudge(metas={"Meta: begin the timeboxing session": True})
    service = MemoryService(str(tmp_path / "memory.db"), judge)
    report = await backfill(path, service)
    assert report.rows_read == 2
    assert report.stored == 1
    assert report.suppressed == {"meta": 1}


async def test_a_judge_failure_stops_the_run(tmp_path):
    path = _legacy_db(
        tmp_path,
        [("Sleep", "sleep at 23:00", "PROFILE", "t1", "2026-03-01T10:00:00")],
    )

    class FailingJudge(StubJudge):
        async def tier(self, observation):
            raise ValueError("model returned nonsense")

    service = MemoryService(str(tmp_path / "memory.db"), FailingJudge())
    with pytest.raises(ValueError, match="model returned nonsense"):
        await backfill(path, service)


async def test_a_fold_is_counted_as_a_fold_not_a_creation(tmp_path):
    path = _legacy_db(
        tmp_path,
        [
            ("Oats", "oats before gym", "PROFILE", "t1", "2026-03-01T10:00:00"),
            ("Oats again", "oats 2h before the gym", "PROFILE", "t2", "2026-03-02T10:00:00"),
        ],
    )

    class FoldingJudge(StubJudge):
        async def canonicalise(self, observation, candidates):
            from memory.judge import CanonicaliseJudgement

            self.calls.append(("canonicalise", observation.uid))
            if candidates:
                return CanonicaliseJudgement(constraint_uid=candidates[0].uid)
            return CanonicaliseJudgement()

    judge = FoldingJudge(
        tiers={
            "Oats: oats before gym": Tier.DURABLE,
            "Oats again: oats 2h before the gym": Tier.DURABLE,
        },
        labels={"Oats: oats before gym": "Oats before gym"},
    )
    service = MemoryService(str(tmp_path / "memory.db"), judge)
    report = await backfill(path, service)
    assert report.constraints_created == 1
    assert report.folds == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_backfill.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.backfill'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/backfill.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from memory.models import Channel
from memory.service import MemoryService, ObserveOutcome


class LegacyRow(BaseModel):
    name: str
    description: str
    thread_ts: str | None
    created_at: datetime


class BackfillReport(BaseModel):
    rows_read: int = 0
    stored: int = 0
    suppressed: dict[str, int] = Field(default_factory=dict)
    constraints_created: int = 0
    folds: int = 0
    durable: int = 0
    outcomes: list[ObserveOutcome] = Field(default_factory=list)


def read_profile_rows(legacy_db_path: str) -> list[LegacyRow]:
    """PROFILE rows from the legacy store, oldest first.

    Read-only; nothing here inspects meaning. Ordering matters because each
    row's canonicalise depends on the constraints prior rows created.
    """
    conn = sqlite3.connect(legacy_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT name, description, thread_ts, created_at "
        "FROM timeboxing_constraints WHERE scope = 'PROFILE' "
        "ORDER BY created_at"
    ).fetchall()
    out: list[LegacyRow] = []
    for r in rows:
        try:
            when = datetime.fromisoformat(str(r["created_at"]))
        except ValueError:
            when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        out.append(
            LegacyRow(
                name=r["name"] or "",
                description=r["description"] or "",
                thread_ts=r["thread_ts"],
                created_at=when,
            )
        )
    return out


def _text(row: LegacyRow) -> str:
    # Serialisation for the judge to read — not a judgement about meaning.
    if row.name and row.description:
        return f"{row.name}: {row.description}"
    return row.name or row.description


async def backfill(legacy_db_path: str, service: MemoryService) -> BackfillReport:
    """Replay legacy PROFILE rows through the real pipeline, sequentially.

    Deliberately does NO dedup of its own: dedup (within-thread) and
    canonicalise (cross-thread) are the only permitted ways to decide two
    rows mean the same thing. Legacy identity never crosses — every
    observation is minted fresh (I3). A judge failure stops the run: a
    half-poisoned store found later is worse than a stopped backfill.
    """
    report = BackfillReport()
    seen_constraints: set[str] = set()
    for row in read_profile_rows(legacy_db_path):
        report.rows_read += 1
        outcome = await service.observe(
            _text(row),
            channel=Channel.PLANNING,
            session_id=row.thread_ts or "legacy:no-thread",
            observed_at=row.created_at,
        )
        report.outcomes.append(outcome)
        if not outcome.stored:
            key = outcome.suppressed_as or "unknown"
            report.suppressed[key] = report.suppressed.get(key, 0) + 1
            continue
        report.stored += 1
        if outcome.constraint_uid in seen_constraints:
            report.folds += 1
        else:
            seen_constraints.add(outcome.constraint_uid)
            report.constraints_created += 1
    report.durable = sum(
        1 for o in report.outcomes if o.tier is not None and o.tier.value == "durable"
    )
    return report


def main() -> None:
    import asyncio
    import os
    import sys

    from memory.openrouter_judge import openrouter_judge_from_env

    legacy = sys.argv[1] if len(sys.argv) > 1 else "data/admonish.db"
    target = sys.argv[2] if len(sys.argv) > 2 else "data/memory.db"
    if os.path.exists(target):
        raise SystemExit(
            f"{target} already exists; refusing to seed over it. "
            f"Move it aside first."
        )
    service = MemoryService(target, openrouter_judge_from_env())
    report = asyncio.run(backfill(legacy, service))
    print(report.model_dump_json(indent=2, exclude={"outcomes"}))
    for o in report.outcomes:
        mark = "stored" if o.stored else f"suppressed:{o.suppressed_as}"
        print(f"  [{mark:18s}] {o.constraint_name or ''}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 76 passed (5 new + 71 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/backfill.py tests/memory/test_backfill.py
git commit -m "feat(memory): backfill — replay legacy PROFILE rows through the real pipeline"
```

---

### Task 4: Run the seeding for real, and write down what it shows

This is the blindspot pass. It is the first real-volume exercise of the whole pipeline — 97 real rows, ~4 model calls each — and its findings feed the #137 decision directly.

- [ ] **Step 1: Run it**

```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.worktrees/memory-observation-log
set -a && . /Users/hugoevers/VScode-projects/admonish-1/.env && set +a
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m memory.backfill \
  /Users/hugoevers/VScode-projects/admonish-1/data/admonish.db data/memory.db \
  | tee .superpowers/sdd/2026-08-17-facade-mcp-and-seeding/backfill-run.log
```

- [ ] **Step 2: Inspect what landed**

Answer, with numbers from the run:
- How many of 97 rows were suppressed, and as what? (Expect meta to catch the "Timeboxing Preference" family.)
- How many constraints were created vs folded? The measured concept count for PROFILE was ~55; is canonicalise in that neighbourhood, or did it under- or over-merge?
- **Spot-check the dangerous direction**: pick three fold decisions and check they really are restatements, not distinct rules merged (the "protein shake vs oats" failure).
- Did the C2F work-project rules (`C2F framing cap 15m`, `Artifact-first scheduling gate`…) land as durable? They are the #116 flood; whether the tier judgement lets them through is a finding either way, not a failure.
- Wall-clock for the run and rough cost.
- `get_active_constraints` for a Monday: how many rows come back, and would that flood a patcher prompt?

- [ ] **Step 3: Write the findings**

Write `docs/superpowers/research/2026-08-17-seeding-run-findings.md` with the numbers, the spot-checks, and — most importantly — **what the run reveals that bears on #137**: which constraints obviously want anchors, which want conditionality, which want decay, and anything that surprised.

- [ ] **Step 4: Commit findings (not the db)**

```bash
git add docs/superpowers/research/2026-08-17-seeding-run-findings.md
git commit -m "docs(memory): findings from seeding the store with the real legacy corpus"
```

`data/memory.db` stays uncommitted.

---

## Not in this plan

- Wiring the thin host (#149 — cross-map, and map A's server is not ready).
- The `McpJudge` (still waiting on map A's tool surface).
- Semantic relevance, promotion, decay, the graph (all behind #137).
