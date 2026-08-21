# Constraint Layer and Read API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive canonical constraints from the observation log, each with a minted durable identity, and serve them to the timeboxing patcher.

**Architecture:** Constraints are **L2** — derived from the immutable L1 observation log, never authored directly. A new `canonicalise` judgement on the existing `Judge` port asks the model whether a new observation expresses a constraint that already exists or a new one; the answer is verified against minted uids before it is acted on. `get_active_constraints` filters by **structural applicability only** — date ranges and days of week, which are comparisons between dates, not judgements about meaning — so no model call sits in the read path.

**Tech Stack:** Python 3.11, pydantic v2, `asyncio`, stdlib `sqlite3`, pytest 8. No new dependencies.

**Builds on:** `2026-08-16-llm-extraction-and-promotion.md`, complete at commit `6167036` — 24 unit tests, 5 eval tests.

**Spec:** `docs/superpowers/specs/2026-08-16-kg-memory-server-design.md`

**Unblocks:** the timebox session (map A) needs a **minted constraint uid**; its journal currently emits `unresolvable` for every constraint because none exists. Task 1 delivers it.

## Global Constraints

Read `CLAUDE.md` first. It governs every task here.

- **No keyword matching, string matching, or regex. `import re` is banned.** No keyword lists, stopword lists, substring tests against user content, split-tokenising, or fuzzy similarity. Every judgement about what user content *means* goes to the model.
- **The exemption, which several tasks here rely on:** operations on identifiers the system itself minted — uids, enum values, SQL column names — and comparisons between **dates** are not judgements about meaning. Filtering a constraint by whether today falls in its date range is arithmetic, not matching.
- **Never trust a model-supplied identifier.** Any uid returned by the judge must be verified against uids the system minted before it is acted on, and an unrecognised id must **raise**. A previous review found this exact defect: acting on an unverified id silently discarded user data, permanently.
- **Independent model calls go out concurrently** via `asyncio.gather(..., return_exceptions=True)`, awaiting all, then re-raising the first exception. The default orphans siblings.
- **No model call in the read path.** `get_active_constraints` must not await a judge.
- **I2 — L1 is append-only.** Constraints are derived; correcting one never edits an observation.
- **I3 — identity is minted, never content-derived.**
- **Extraction model:** `google/gemini-3.6-flash` with `"reasoning": {"effort": "minimal"}`. Never `{"enabled": false}` — the endpoint rejects it.
- Unit tests stub the judge and run offline. Eval tests hit OpenRouter and are marked `@pytest.mark.slow`.
- Zero imports from `fateforger.*`. `from __future__ import annotations` at the top of every module. Type annotations on public functions.
- Tests in `tests/memory/`. **Never create `tests/memory/__init__.py` or `tests/__init__.py`.**

## Spec conformance, checked explicitly

Last time a plan in this project silently contradicted the spec it implemented. Checked here
before execution, invariant by invariant:

| | |
|---|---|
| **I1** — LLM proposes and names; no model in the read path | `canonicalise` is write-time. `get_active_constraints` awaits nothing. ✅ |
| **I2** — L1 append-only | Constraints live in their own table. No task writes to `observations`. ✅ |
| **I3** — identity minted, never content-derived | `Constraint.uid = mint_uid()`. Model-returned uids are verified against minted ones and raise otherwise, in both `ingest` and `project`. ✅ |
| **I4** — taxonomy change is re-projection, not migration | `source_observation_uids` is the provenance link that makes re-projection possible. ✅ |
| **I5** — every write is compare-and-swap | ⚠️ **Gap, accepted deliberately — see below.** |
| **I6** — promotion by structure, rejection by statistics | Not exercised; promotion is a later plan. n/a |

**The I5 gap, stated rather than hidden.** `ConstraintStore.upsert` is last-write-wins
(`ON CONFLICT DO UPDATE`), and `project` does a read-modify-write when appending an observation
uid to an existing constraint. Two concurrent projections of different observations onto the
same constraint could lose one provenance link.

Accepted for now because the write path is single-threaded per session and the loss is a
provenance link rather than user data — the observation itself is safe in the append-only log,
so a later re-projection recovers it. **It must be closed before any concurrent writer exists.**
Recorded here rather than discovered later.

**Why `canonicalise` is not parallelised with the other four judgements**, given the standing
rule that independent calls go out together: it is not independent. It needs the observation
already stored (for its uid) and needs `ingest`'s tier decision, so it is genuinely sequential
after ingest rather than alongside it.

## What already exists

At commit `6167036`:

- `src/memory/identity.py` — `mint_uid() -> str`
- `src/memory/models.py` — `Channel`, `Provenance`, `Reliability`, `Tier`, `Observation`
- `src/memory/store.py` — `ObservationStore`
- `src/memory/judge.py` — `Judge` protocol (`anchors`, `tier`, `meta`, `dedup`), four result models, `StubJudge`
- `src/memory/ingest.py` — `ingest`, `IngestResult`
- `src/memory/openrouter_judge.py` — `OpenRouterJudge`

## File Structure

| File | Responsibility |
|---|---|
| `src/memory/constraint.py` | `Applicability`, `Constraint`, `ConstraintView` |
| `src/memory/constraint_store.py` | Constraint persistence with provenance back to observations |
| `src/memory/read_api.py` | `get_active_constraints` — structural filtering, no model call |
| `src/memory/judge.py` | extended with the `canonicalise` judgement |
| `src/memory/projection.py` | Observation → Constraint |

---

### Task 1: The Constraint entity and its store

This is what the timebox session is blocked on. A constraint gets a minted uid the moment it exists, so their journal can stop emitting `unresolvable`.

**Files:**
- Create: `src/memory/constraint.py`
- Create: `src/memory/constraint_store.py`
- Test: `tests/memory/test_constraint_store.py`

**Interfaces:**
- Consumes: `mint_uid`, `Tier`.
- Produces: `Applicability` (`start_date: date | None`, `end_date: date | None`, `days_of_week: list[int]`); `Constraint` (`uid`, `name`, `description`, `necessity`, `scope`, `status`, `source`, `frame_slot: str | None`, `tier`, `applicability`, `source_observation_uids: list[str]`, `created_at`); `ConstraintView` — the six fields the patcher consumes plus `frame_slot`; `Constraint.to_view() -> ConstraintView`; `ConstraintStore(db_path)` with `.upsert(c) -> str`, `.get(uid) -> Constraint | None`, `.all() -> list[Constraint]`, `.durable() -> list[Constraint]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_constraint_store.py
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint import Applicability, Constraint, ConstraintView
from memory.constraint_store import ConstraintStore
from memory.models import Tier

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _c(name: str = "Oats before gym", **kw) -> Constraint:
    defaults = dict(
        name=name,
        description="Eat oats two hours before gym",
        necessity="must",
        scope="profile",
        status="proposed",
        source="user",
        frame_slot="pre_gym_meal",
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=["obs-1"],
        created_at=T0,
    )
    defaults.update(kw)
    return Constraint(**defaults)


def test_a_constraint_gets_a_minted_uid(tmp_path):
    """The timebox journal is blocked on this existing."""
    a, b = _c(), _c()
    assert a.uid and b.uid
    assert a.uid != b.uid, "identity must not be derived from content"


def test_upsert_and_get_round_trip(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    uid = store.upsert(c)
    got = store.get(uid)
    assert got is not None
    assert got.name == "Oats before gym"
    assert got.frame_slot == "pre_gym_meal"
    assert got.tier is Tier.DURABLE
    assert got.source_observation_uids == ["obs-1"]


def test_upsert_by_uid_replaces_and_does_not_duplicate(tmp_path):
    """A constraint is derived, so re-projection must not accumulate copies."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    store.upsert(c)
    c.description = "Eat oats 2h before any sport"
    c.source_observation_uids = ["obs-1", "obs-2"]
    store.upsert(c)
    assert len(store.all()) == 1
    assert store.get(c.uid).description == "Eat oats 2h before any sport"


def test_durable_filters_by_tier(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Oats before gym", tier=Tier.DURABLE))
    store.upsert(_c("Hockey at 11:45 today", tier=Tier.SESSION))
    durable = store.durable()
    assert len(durable) == 1
    assert durable[0].name == "Oats before gym"


def test_to_view_carries_the_seven_fields_the_patcher_needs(tmp_path):
    view = _c().to_view()
    assert isinstance(view, ConstraintView)
    assert view.name == "Oats before gym"
    assert view.necessity == "must"
    assert view.scope == "profile"
    assert view.status == "proposed"
    assert view.source == "user"
    assert view.description == "Eat oats two hours before gym"
    assert view.frame_slot == "pre_gym_meal"


def test_applicability_defaults_to_always(tmp_path):
    a = Applicability()
    assert a.start_date is None
    assert a.end_date is None
    assert a.days_of_week == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_constraint_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.constraint'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/constraint.py
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from memory.identity import mint_uid
from memory.models import Tier


class Applicability(BaseModel):
    """When a constraint applies, expressed structurally.

    Every field here is compared arithmetically at read time — a date against
    a range, a weekday against a list of weekdays. None of it is a judgement
    about meaning, so the read path needs no model call.
    """

    start_date: date | None = None
    end_date: date | None = None
    days_of_week: list[int] = Field(default_factory=list)  # 0=Mon .. 6=Sun

    def applies_on(self, day: date) -> bool:
        if self.start_date is not None and day < self.start_date:
            return False
        if self.end_date is not None and day > self.end_date:
            return False
        if self.days_of_week and day.weekday() not in self.days_of_week:
            return False
        return True


class ConstraintView(BaseModel):
    """What the timeboxing patcher consumes.

    Deliberately narrow: the patcher renders these and nothing else. Memory
    owns relevance filtering so the two sides cannot diverge.
    """

    uid: str
    name: str
    description: str
    necessity: str
    scope: str
    status: str
    source: str
    frame_slot: str | None = None


class Constraint(BaseModel):
    """A canonical rule, derived from one or more observations.

    L2: never authored directly, always projected from the immutable log.
    `source_observation_uids` is the provenance link back to L1 — it is what
    makes re-projection possible when the taxonomy changes (I4).
    """

    uid: str = Field(default_factory=mint_uid)
    name: str
    description: str
    necessity: str
    scope: str
    status: str
    source: str
    frame_slot: str | None = None
    tier: Tier = Tier.SESSION
    applicability: Applicability = Field(default_factory=Applicability)
    source_observation_uids: list[str] = Field(default_factory=list)
    created_at: datetime

    def to_view(self) -> ConstraintView:
        return ConstraintView(
            uid=self.uid,
            name=self.name,
            description=self.description,
            necessity=self.necessity,
            scope=self.scope,
            status=self.status,
            source=self.source,
            frame_slot=self.frame_slot,
        )
```

```python
# src/memory/constraint_store.py
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from memory.constraint import Applicability, Constraint
from memory.models import Tier

_SCHEMA = """
CREATE TABLE IF NOT EXISTS constraints (
    uid          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL,
    necessity    TEXT NOT NULL,
    scope        TEXT NOT NULL,
    status       TEXT NOT NULL,
    source       TEXT NOT NULL,
    frame_slot   TEXT,
    tier         TEXT NOT NULL,
    applicability TEXT NOT NULL,
    source_observation_uids TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_constraints_tier ON constraints(tier);
"""


class ConstraintStore:
    """Persistence for derived constraints.

    Unlike the observation log this is NOT append-only: a constraint is a
    projection, so re-projecting replaces it in place. Its provenance
    (source_observation_uids) points back at the immutable log, which is
    where the history actually lives.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert(self, constraint: Constraint) -> str:
        self._conn.execute(
            "INSERT INTO constraints (uid, name, description, necessity, scope, "
            " status, source, frame_slot, tier, applicability, "
            " source_observation_uids, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET "
            " name=excluded.name, description=excluded.description, "
            " necessity=excluded.necessity, scope=excluded.scope, "
            " status=excluded.status, source=excluded.source, "
            " frame_slot=excluded.frame_slot, tier=excluded.tier, "
            " applicability=excluded.applicability, "
            " source_observation_uids=excluded.source_observation_uids",
            (
                constraint.uid,
                constraint.name,
                constraint.description,
                constraint.necessity,
                constraint.scope,
                constraint.status,
                constraint.source,
                constraint.frame_slot,
                constraint.tier.value,
                constraint.applicability.model_dump_json(),
                json.dumps(constraint.source_observation_uids),
                constraint.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return constraint.uid

    def get(self, uid: str) -> Constraint | None:
        row = self._conn.execute(
            "SELECT * FROM constraints WHERE uid = ?", (uid,)
        ).fetchone()
        return self._row(row) if row else None

    def all(self) -> list[Constraint]:
        rows = self._conn.execute(
            "SELECT * FROM constraints ORDER BY created_at"
        ).fetchall()
        return [self._row(r) for r in rows]

    def durable(self) -> list[Constraint]:
        rows = self._conn.execute(
            "SELECT * FROM constraints WHERE tier = ? ORDER BY created_at",
            (Tier.DURABLE.value,),
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> Constraint:
        return Constraint(
            uid=row["uid"],
            name=row["name"],
            description=row["description"],
            necessity=row["necessity"],
            scope=row["scope"],
            status=row["status"],
            source=row["source"],
            frame_slot=row["frame_slot"],
            tier=Tier(row["tier"]),
            applicability=Applicability.model_validate_json(row["applicability"]),
            source_observation_uids=json.loads(row["source_observation_uids"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 30 passed (6 new + 24 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/constraint.py src/memory/constraint_store.py tests/memory/test_constraint_store.py
git commit -m "feat(memory): Constraint entity with minted uid, and its store"
```

---

### Task 2: `get_active_constraints` — the read call

What the patcher calls. No model in this path.

**Files:**
- Create: `src/memory/read_api.py`
- Test: `tests/memory/test_read_api.py`

**Interfaces:**
- Consumes: `ConstraintStore`, `Constraint`, `ConstraintView`, `Applicability`.
- Produces: `get_active_constraints(store: ConstraintStore, day: date, stage: str | None = None) -> list[ConstraintView]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_read_api.py
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint import Applicability, Constraint, ConstraintView
from memory.constraint_store import ConstraintStore
from memory.models import Tier
from memory.read_api import get_active_constraints

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
MONDAY = date(2026, 3, 9)
SUNDAY = date(2026, 3, 8)


def _c(name: str, tier: Tier = Tier.DURABLE, **app) -> Constraint:
    return Constraint(
        name=name,
        description=f"description of {name}",
        necessity="must",
        scope="profile",
        status="locked",
        source="user",
        frame_slot=None,
        tier=tier,
        applicability=Applicability(**app),
        source_observation_uids=[],
        created_at=T0,
    )


def test_returns_durable_constraints_as_views(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Sleep 23:00"))
    result = get_active_constraints(store, MONDAY)
    assert len(result) == 1
    assert isinstance(result[0], ConstraintView)
    assert result[0].name == "Sleep 23:00"


def test_session_tier_constraints_are_excluded(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Hockey today", tier=Tier.SESSION))
    assert get_active_constraints(store, MONDAY) == []


def test_day_of_week_filtering_is_structural(tmp_path):
    """Comparing a weekday to a list of weekdays is arithmetic, not matching."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Client on Tue and Thu", days_of_week=[1, 3]))
    assert get_active_constraints(store, MONDAY) == []
    assert len(get_active_constraints(store, date(2026, 3, 10))) == 1


def test_date_range_filtering(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(
        _c("Sprint focus", start_date=date(2026, 3, 1), end_date=date(2026, 3, 5))
    )
    assert get_active_constraints(store, MONDAY) == []
    assert len(get_active_constraints(store, date(2026, 3, 3))) == 1


def test_a_constraint_with_no_applicability_applies_every_day(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Sleep 23:00"))
    assert len(get_active_constraints(store, MONDAY)) == 1
    assert len(get_active_constraints(store, SUNDAY)) == 1


def test_the_view_carries_the_uid_so_the_journal_can_join(tmp_path):
    """Map A's journal emits `unresolvable` without this."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c("Sleep 23:00")
    store.upsert(c)
    assert get_active_constraints(store, MONDAY)[0].uid == c.uid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_read_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.read_api'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/read_api.py
from __future__ import annotations

from datetime import date

from memory.constraint import ConstraintView
from memory.constraint_store import ConstraintStore


def get_active_constraints(
    store: ConstraintStore, day: date, stage: str | None = None
) -> list[ConstraintView]:
    """Constraints that apply on `day`, as views for the patcher.

    No model call happens here. Filtering is structural only — a date against
    a range, a weekday against a list of weekdays — which is arithmetic, not
    a judgement about meaning.

    LIMITATION, deliberate and worth stating: this returns every durable
    constraint whose applicability window covers the day. It does NOT do
    semantic relevance — "which of these matter for a day containing hockey"
    — because that requires the anchor graph, which is a later plan. Until
    then the caller may receive more constraints than are useful. The patcher
    renders whatever it is handed by agreement, so memory owning this filter
    is what keeps the two sides from diverging as it improves.

    `stage` is accepted and currently unused; it is part of the agreed call
    shape and will select stage-relevant constraints once the graph exists.
    """
    return [c.to_view() for c in store.durable() if c.applicability.applies_on(day)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 36 passed (6 new + 30 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/read_api.py tests/memory/test_read_api.py
git commit -m "feat(memory): get_active_constraints with structural applicability filtering"
```

---

### Task 3: The `canonicalise` judgement

Asks the model whether a new observation expresses a constraint that already exists. This is the cross-session counterpart to `dedup`, which is within-session only.

**Files:**
- Modify: `src/memory/judge.py` (add the judgement, extend `StubJudge`)
- Modify: `src/memory/openrouter_judge.py` (implement it)
- Test: `tests/memory/test_canonicalise.py`

**Interfaces:**
- Produces: `CanonicaliseJudgement` (`constraint_uid: str | None`, `rationale: str`); `Judge.canonicalise(observation, candidates: list[Constraint]) -> CanonicaliseJudgement`; `StubJudge(canonical={text: uid})`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_canonicalise.py
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from memory.constraint import Applicability, Constraint
from memory.judge import CanonicaliseJudgement, StubJudge
from memory.models import Channel, Observation, Provenance, Tier
from memory.openrouter_judge import OpenRouterJudge

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def _c(name: str) -> Constraint:
    return Constraint(
        name=name,
        description=name,
        necessity="must",
        scope="profile",
        status="locked",
        source="user",
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[],
        created_at=T0,
    )


def _mock(payload: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_stub_returns_its_canned_answer():
    judge = StubJudge(canonical={"oats before gym": "c-1"})
    result = await judge.canonicalise(_obs("oats before gym"), [])
    assert isinstance(result, CanonicaliseJudgement)
    assert result.constraint_uid == "c-1"


async def test_stub_default_is_a_new_constraint():
    """An unstubbed question must never silently merge into an existing rule."""
    judge = StubJudge()
    result = await judge.canonicalise(_obs("anything"), [])
    assert result.constraint_uid is None


async def test_no_candidates_short_circuits_without_a_call():
    judge = OpenRouterJudge(
        api_key="k", base_url="https://example.invalid", client=_mock({})
    )
    result = await judge.canonicalise(_obs("anything"), [])
    assert result.constraint_uid is None


async def test_openrouter_parses_a_match():
    existing = _c("Oats before gym")
    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"constraint_uid": existing.uid, "rationale": "same rule"}),
    )
    result = await judge.canonicalise(_obs("eat oats 2h before gym"), [existing])
    assert result.constraint_uid == existing.uid


async def test_a_malformed_response_fails_loudly():
    import pytest

    judge = OpenRouterJudge(
        api_key="k",
        base_url="https://example.invalid",
        client=_mock({"unexpected": "shape"}),
    )
    with pytest.raises(ValueError, match="could not parse"):
        await judge.canonicalise(_obs("anything"), [_c("Oats before gym")])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_canonicalise.py -v`
Expected: FAIL with `ImportError: cannot import name 'CanonicaliseJudgement'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/memory/judge.py`:

```python
class CanonicaliseJudgement(BaseModel):
    """Which existing constraint, if any, a new observation expresses."""

    constraint_uid: str | None = None
    rationale: str = ""
```

Add to the `Judge` protocol:

```python
    async def canonicalise(
        self, observation: Observation, candidates: list[object]
    ) -> CanonicaliseJudgement: ...
```

Add to `StubJudge.__init__` a `canonical: dict[str, str] | None = None` parameter stored as
`self._canonical = canonical or {}`, and this method:

```python
    async def canonicalise(
        self, observation: Observation, candidates: list[object]
    ) -> CanonicaliseJudgement:
        self.calls.append(("canonicalise", observation.uid))
        return CanonicaliseJudgement(
            constraint_uid=self._canonical.get(observation.text)
        )
```

Add to `src/memory/openrouter_judge.py`:

```python
CANONICALISE_PROMPT = """\
You decide whether a new statement expresses a rule the system already knows.

You are given a new statement and a list of existing rules, each with an id.
Return the id of the rule the statement expresses, or null if it expresses a
rule that is genuinely new.

The same rule restated, reworded, or given in more detail is the SAME rule.
A different rule about the same topic is NOT — "oats before gym" and "protein
after gym" are two rules about gym nutrition, not one.

Respond with JSON only:
{"constraint_uid": "<id>"|null, "rationale": "..."}\
"""
```

and this method:

```python
    async def canonicalise(
        self, observation: Observation, candidates: list[object]
    ) -> CanonicaliseJudgement:
        if not candidates:
            # Nothing to match against; "new" is the only possible answer and
            # asking would waste a call. This is a shortcut, not a fallback.
            return CanonicaliseJudgement()
        listing = json.dumps(
            [{"uid": c.uid, "name": c.name, "description": c.description} for c in candidates],
            ensure_ascii=False,
        )
        user = (
            f"New statement:\n{json.dumps(observation.text, ensure_ascii=False)}"
            f"\n\nExisting rules:\n{listing}"
        )
        payload = await self._ask(CANONICALISE_PROMPT, user)
        if "constraint_uid" not in payload:
            raise ValueError(f"could not parse judge response: {payload!r}")
        return self._build(CanonicaliseJudgement, payload)
```

Import `CanonicaliseJudgement` in `openrouter_judge.py` alongside the other judgements.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 41 passed (5 new + 36 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/judge.py src/memory/openrouter_judge.py tests/memory/test_canonicalise.py
git commit -m "feat(memory): canonicalise judgement — does this observation express a known rule"
```

---

### Task 4: Projection — observations into constraints

Where an observation becomes, or joins, a constraint. The model-returned uid is verified before use.

**Files:**
- Create: `src/memory/projection.py`
- Test: `tests/memory/test_projection.py`

**Interfaces:**
- Consumes: `Judge`, `Observation`, `Constraint`, `ConstraintStore`, `IngestResult`.
- Produces: `async project(observation, ingest_result, judge, constraint_store) -> Constraint`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_projection.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.constraint import Applicability, Constraint
from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import StubJudge
from memory.models import Channel, Observation, Provenance, Tier
from memory.projection import project

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def _result(tier: Tier = Tier.DURABLE) -> IngestResult:
    return IngestResult(stored=True, uid="obs-1", tier=tier, anchors=["gym"])


def _existing(store: ConstraintStore, name: str) -> Constraint:
    c = Constraint(
        name=name,
        description=name,
        necessity="must",
        scope="profile",
        status="proposed",
        source="user",
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=["obs-0"],
        created_at=T0,
    )
    store.upsert(c)
    return c


async def test_a_new_observation_creates_a_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("eat oats two hours before gym")
    c = await project(obs, _result(), StubJudge(), store)
    assert c.uid
    assert c.tier is Tier.DURABLE
    assert c.source_observation_uids == [obs.uid]
    assert len(store.all()) == 1


async def test_a_restatement_joins_the_existing_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    existing = _existing(store, "Oats before gym")
    obs = _obs("oats 2h before the gym")
    judge = StubJudge(canonical={"oats 2h before the gym": existing.uid})
    c = await project(obs, _result(), judge, store)
    assert c.uid == existing.uid
    assert obs.uid in c.source_observation_uids
    assert "obs-0" in c.source_observation_uids
    assert len(store.all()) == 1, "must not create a second constraint"


async def test_an_unknown_constraint_uid_raises(tmp_path):
    """Never act on a model-supplied id that was never minted."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    _existing(store, "Oats before gym")
    judge = StubJudge(canonical={"anything": "not-a-real-uid"})
    with pytest.raises(ValueError, match="unknown constraint_uid"):
        await project(_obs("anything"), _result(), judge, store)


async def test_a_session_tier_observation_still_projects(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = await project(_obs("hockey at 11:45"), _result(Tier.SESSION), StubJudge(), store)
    assert c.tier is Tier.SESSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_projection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memory.projection'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/memory/projection.py
from __future__ import annotations

from memory.constraint import Applicability, Constraint
from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import Judge
from memory.models import Observation


async def project(
    observation: Observation,
    ingest_result: IngestResult,
    judge: Judge,
    constraint_store: ConstraintStore,
) -> Constraint:
    """Turn a stored observation into, or fold it into, a constraint.

    L2 is derived from L1: the constraint records which observations produced
    it, so re-projection is possible when the taxonomy changes (I4).
    """
    candidates = constraint_store.all()
    judgement = await judge.canonicalise(observation, candidates)

    if judgement.constraint_uid is not None:
        # The id came from the model. Verify it names a constraint we actually
        # minted before folding user data into it — set membership over
        # system-minted uids, explicitly outside the no-matching rule.
        known = {c.uid: c for c in candidates}
        if judgement.constraint_uid not in known:
            raise ValueError(
                f"judge returned unknown constraint_uid "
                f"{judgement.constraint_uid!r}; not among {len(known)} candidates"
            )
        existing = known[judgement.constraint_uid]
        if observation.uid not in existing.source_observation_uids:
            existing.source_observation_uids.append(observation.uid)
        existing.tier = ingest_result.tier
        constraint_store.upsert(existing)
        return existing

    created = Constraint(
        name=observation.text,
        description=observation.text,
        necessity="should",
        scope="profile",
        status="proposed",
        source=observation.channel.value,
        tier=ingest_result.tier,
        applicability=Applicability(),
        source_observation_uids=[observation.uid],
        created_at=observation.observed_at,
    )
    constraint_store.upsert(created)
    return created
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 45 passed (4 new + 41 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/projection.py tests/memory/test_projection.py
git commit -m "feat(memory): project observations into canonical constraints"
```

---

### Task 5: Eval the canonicalise prompt

Unit tests prove the plumbing. Only an eval proves the prompt distinguishes a restatement from a different rule about the same topic — the judgement the whole layer rests on.

**Files:**
- Modify: `tests/memory/test_eval_extraction.py`

- [ ] **Step 1: Write the eval tests**

Append to `tests/memory/test_eval_extraction.py`:

```python
def _constraint(name: str, description: str):
    from datetime import datetime, timezone

    from memory.constraint import Applicability, Constraint
    from memory.models import Tier

    return Constraint(
        name=name,
        description=description,
        necessity="must",
        scope="profile",
        status="locked",
        source="user",
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[],
        created_at=datetime(2026, 3, 9, tzinfo=timezone.utc),
    )


async def test_a_reworded_restatement_is_recognised_as_the_same_rule():
    existing = _constraint("Oats before gym", "Eat oats two hours before the gym")
    async with _judge() as judge:
        result = await judge.canonicalise(
            _obs("I need my oats a couple of hours ahead of training"), [existing]
        )
    assert result.constraint_uid == existing.uid


async def test_a_different_rule_on_the_same_topic_is_not_merged():
    """The failure that would silently destroy a real preference."""
    existing = _constraint("Oats before gym", "Eat oats two hours before the gym")
    async with _judge() as judge:
        result = await judge.canonicalise(
            _obs("Protein shake within 30 minutes after the gym"), [existing]
        )
    assert result.constraint_uid is None


async def test_an_unrelated_statement_is_new():
    existing = _constraint("Oats before gym", "Eat oats two hours before the gym")
    async with _judge() as judge:
        result = await judge.canonicalise(
            _obs("Never schedule meetings before 13:00"), [existing]
        )
    assert result.constraint_uid is None
```

- [ ] **Step 2: Run the evals**

```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.worktrees/memory-observation-log
set -a && . /Users/hugoevers/VScode-projects/admonish-1/.env && set +a
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_eval_extraction.py -v -m slow
```
Expected: **8 passed** (3 new + 5 existing). If any fails, that is a prompt defect — report it with the model's actual response, and do not weaken the assertion.

- [ ] **Step 3: Commit**

```bash
git add tests/memory/test_eval_extraction.py
git commit -m "test(memory): eval the canonicalise prompt against the live model"
```

---

## Not in this plan

- **Semantic relevance in `get_active_constraints`.** Needs the anchor graph (#137's encoding fork). Until then the read call filters structurally and may return more than is useful — stated as a limitation in the docstring rather than hidden.
- **The `McpJudge`** — waiting on map A's tool surface, which lands in their task 15.
- **Promotion by anchor recurrence, decay, the gate, the ambient proposal surface.** All build on this layer.
- **Migrating the 1,662 legacy rows.** Open question in the spec.
