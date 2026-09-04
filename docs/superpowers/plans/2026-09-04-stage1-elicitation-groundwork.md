# Stage 1 Elicitation Groundwork Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land every shape the Stage 1 spikes (#284 A′, #286 C) read, so that a session that confirms its day now shows the active constraints, proposes to close Stage 1 with a typed gate, and offers Next only then; the classify/generate judges themselves are the spikes and are not built here.

**Architecture:** The memory server's view gains anchors, joined in the read path over uids it minted. The kernel gains three fact kinds with stable ids, a typed `Gate`, a `GateMet` outcome and a three-state Stage 1 marker on the snapshot; one arithmetic function `stage1_gate(snapshot)` decides the gate for both the outcome and the interpreter's decision set. Cells are forty static soft user-owned catalog requirements, and every requirement carries its stage so the card stops mapping fact kinds to stages. The host's resolve hands the active rules to the snapshot on every turn; the brief drops rules a `SUSPENDED_CONSTRAINT` fact names. The feedback observer is a port with the call site wired and a recording implementation; its transport is a follow-up.

**Tech Stack:** Python 3.11, pydantic v2, sqlite3 (memory server), pytest + pytest-asyncio. Memory-server code runs with `PYTHONPATH=src`. Tests run with `.venv/bin/python -m pytest`.

## Global Constraints

Copied from the spec and CLAUDE.md. Every task's requirements include these.

- **No keyword matching, string matching, or regex on user content. Ever.** String operations on identifiers this system minted (uids, fact ids, requirement ids, enum values) are the documented exception.
- **The read path never calls a model.** `get_active_constraints`, `stage1_gate`, ranking and the catalog's `_is_satisfied` are arithmetic over the snapshot and the store.
- **`src/memory/` imports nothing from `fateforger.*`.** Run anything under it with `PYTHONPATH=src`.
- **Absence and failure never read as data.** A missing store raises `AdaptiveDependencyUnavailable`; a missing matrix fact means "no elicitation has run", not "gate unmet".
- **Stable fact ids:** `coverage:{day}`, `suspend:{uid}`, `elicited:{cell}:{uuid}`. Facts merge by id.
- **Next is offered only on `GateMet`**, and `stage1_gate` is the one function that decides it for the kernel and for `_display_context`.
- **Unit tests stub the model and assert the decision, never an output string.** Every guard is mutation-verified: neuter it, watch the test fail, restore it.
- **`tests/conftest.py` pins `FF_TIMEBOX_BACKEND=legacy`;** Slack-surface tests that need the harness path opt in with `monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")`.
- **Nothing here reads or writes `data/memory.db`.** Unit fixtures build their own store in `tmp_path`; the eval fixture copies a store into `tmp_path` and is marked `slow`.
- **Commit after every task.** Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

Two refinements of the spec's wording, decided while planning, so the implementer does not re-derive them:

1. The anchor join runs inside `read_api.get_active_constraints` behind an optional `anchors: AnchorStore` argument. `MemoryService` passes its own store, so the join still happens "in the service" as the spec says, and the read-only KG client can pass a store of its own without needing a `Judge` to construct a `MemoryService`.
2. Suspension is enforced where the brief is built, `AdaptiveTimeboxing._build_brief`, not in the host's `resolve`. The host returns unfiltered rows so the card can render a suspended row *as* suspended; the brief drops it. Same outcome, one place.

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `src/memory/constraint.py` | `AnchorRef`, `ConstraintView.anchors`, `ConstraintView.fade` | 1, 1b |
| `src/memory/read_api.py` | `get_active_constraints(..., anchors=)` attaches refs | 1 |
| `src/memory/service.py` | passes `self._anchors` | 1 |
| `src/fateforger/agents/timeboxing/kg_constraint_client.py` | opens an `AnchorStore`, row carries `anchors` and `fade`, `count_suspended` | 2 |
| `src/fateforger/agents/timeboxing/session_contracts.py` | fact kinds, id helpers, `filed_by`, `CellRef`, `Gate`, `GateMet`, `DenyAssumption`, `FileAssumption`, snapshot fields | 3 |
| `src/fateforger/agents/timeboxing/elicitation.py` (new) | concern-floor, criteria, rows, `ALL_CELLS`, `CoverageMatrix`, `coverage_matrix()`, `stage1_gate()` | 4 |
| `src/fateforger/agents/timeboxing/readiness.py` | `stage` and `cell` on `ArtifactRequirement`, forty cell requirements, `stage_of`, per-cell satisfaction | 5 |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | new intents, Stage 1 gate in the run loop, snapshot rows, brief filter, Back rung | 6 |
| `src/fateforger/slack_bot/timeboxing_host.py` | `resolve(SKELETON)` returns the rules it already fetched | 7 |
| `src/fateforger/slack_bot/timeboxing_intents.py` | Stage 1 decision set, new drafts, binding, button meta | 8 |
| `src/fateforger/slack_bot/stage_cards.py`, `timeboxing_cards.py` | stage from requirement, `NextControl`, `gate` line, `GateMet` card | 9 |
| `src/fateforger/agents/timeboxing/feedback.py` (new), `src/fateforger/slack_bot/handlers.py` | `FeedbackObserver` port, recording implementation, call after save | 10 |
| `tests/fixtures/stage1/` (new), `tests/evals/test_stage1_fixture.py` (new) | the shared fixture from #283 | 11 |

---

### Task 1: Anchors on the memory view

**Files:**
- Modify: `src/memory/constraint.py:106-121` (`ConstraintView`)
- Modify: `src/memory/read_api.py:10-56` (`get_active_constraints`)
- Modify: `src/memory/service.py:204-230` (`get_active_constraints`)
- Test: `tests/memory/test_view_anchors.py` (new)

**Interfaces:**
- Consumes: `AnchorStore.anchors_for(constraint_uid) -> list[str]`, `AnchorStore.get(uid) -> Anchor | None` (`src/memory/anchor_store.py:98,160`), `Anchor(uid, name)`.
- Produces: `AnchorRef(uid: str, name: str)`; `ConstraintView.anchors: list[AnchorRef]` (default empty); `get_active_constraints(store, day, stage=None, *, reachable=None, day_type=None, anchors: AnchorStore | None = None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_view_anchors.py
"""The view carries the anchors a rule attaches to, by uid and name.

The join is set membership and a lookup over uids this system minted, so the
read path stays model-free (I1). Without `anchors=` the view is exactly what it
was, so every existing caller keeps its shape.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.anchor import Anchor
from memory.anchor_store import AnchorStore
from memory.constraint import AnchorRef, Constraint, Necessity, Scope, Source, Status
from memory.constraint_store import ConstraintStore
from memory.models import Tier
from memory.read_api import get_active_constraints

DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _rule(name: str) -> Constraint:
    return Constraint(
        name=name,
        description=f"{name} description",
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        created_at=NOW,
        last_observed_at=NOW,
    )


def _stores(tmp_path) -> tuple[ConstraintStore, AnchorStore]:
    db = str(tmp_path / "memory.db")
    return ConstraintStore(db), AnchorStore(db)


def test_a_linked_rule_carries_its_anchor_uid_and_name(tmp_path) -> None:
    constraints, anchors = _stores(tmp_path)
    gym = Anchor(name="gym")
    anchors.upsert(gym)
    rule = _rule("Oats before gym")
    constraints.upsert(rule)
    anchors.replace_constraint_links(rule.uid, [gym.uid])

    [view] = get_active_constraints(constraints, DAY, anchors=anchors)

    assert view.anchors == [AnchorRef(uid=gym.uid, name="gym")]


def test_an_unlinked_rule_has_an_empty_list_not_a_missing_field(tmp_path) -> None:
    constraints, anchors = _stores(tmp_path)
    constraints.upsert(_rule("Plan at 17:00"))

    [view] = get_active_constraints(constraints, DAY, anchors=anchors)

    assert view.anchors == []


def test_without_an_anchor_store_the_view_is_unchanged(tmp_path) -> None:
    constraints, anchors = _stores(tmp_path)
    gym = Anchor(name="gym")
    anchors.upsert(gym)
    rule = _rule("Oats before gym")
    constraints.upsert(rule)
    anchors.replace_constraint_links(rule.uid, [gym.uid])

    [view] = get_active_constraints(constraints, DAY)

    assert view.anchors == []
    assert "anchors" in view.model_dump()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/memory/test_view_anchors.py -v`
Expected: FAIL with `ImportError: cannot import name 'AnchorRef'`.

- [ ] **Step 3: Add `AnchorRef` and the field**

In `src/memory/constraint.py`, directly above `class ConstraintView`:

```python
class AnchorRef(BaseModel):
    """One anchor a rule attaches to, as the patcher and the card need it.

    Both fields are minted by this system: the uid at `resolve_anchors`, the
    name by the model that judged the statement. A card groups by `name` and
    steers by the constraint uid, never by this one.
    """

    uid: str
    name: str
```

In `ConstraintView`, after `frame_slot: str | None = None`:

```python
    #: Empty for an unanchored rule. Unanchored and unreachable are different
    #: things; a card renders these in their own group rather than dropping them.
    anchors: list[AnchorRef] = Field(default_factory=list)
```

`Field` is already imported in that module.

- [ ] **Step 4: Attach anchors in the read path**

In `src/memory/read_api.py`, change the signature and the return:

```python
from memory.anchor_store import AnchorStore
from memory.constraint import AnchorRef, Constraint, ConstraintView, Necessity


def get_active_constraints(
    store: ConstraintStore,
    day: date,
    stage: str | None = None,
    *,
    reachable: set[str] | None = None,
    day_type: str | None = None,
    anchors: AnchorStore | None = None,
) -> list[ConstraintView]:
```

Add to the docstring, before the `stage` paragraph:

```
    `anchors`, when given, attaches each rule's anchors to its view as
    `(uid, name)` pairs. It is a lookup over uids this system minted and adds
    no judgement to the read path. Omitting it returns views with an empty
    `anchors` list, which is what every caller before 2026-09-04 received.
```

Replace the final `return` with:

```python
    ordered = sorted(applicable, key=_reading_order)
    return [_attach_anchors(c.to_view(), c.uid, anchors) for c in ordered]


def _attach_anchors(
    view: ConstraintView, constraint_uid: str, anchors: AnchorStore | None
) -> ConstraintView:
    if anchors is None:
        return view
    refs: list[AnchorRef] = []
    for anchor_uid in anchors.anchors_for(constraint_uid):
        anchor = anchors.get(anchor_uid)
        if anchor is not None:
            refs.append(AnchorRef(uid=anchor.uid, name=anchor.name))
    return view.model_copy(update={"anchors": refs})
```

- [ ] **Step 5: Pass the service's own store**

In `src/memory/service.py`, `get_active_constraints`, change the final call:

```python
        return _read(
            self._constraints,
            day,
            stage,
            reachable=reachable,
            day_type=day_type,
            anchors=self._anchors,
        )
```

- [ ] **Step 6: Run the new test and the memory suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/memory/test_view_anchors.py tests/memory/test_active_constraint_ordering.py tests/memory/test_anchor_graph.py -v`
Expected: all PASS. The MCP tool `memory_get_active_constraints` serialises views with `model_dump(mode="json")`, so its output gains an `anchors` key with no code change.

- [ ] **Step 7: Mutation check**

Temporarily make `_attach_anchors` return `view` unconditionally. Run the first test. Expected: FAIL on the equality. Restore.

- [ ] **Step 8: Commit**

```bash
git add src/memory/constraint.py src/memory/read_api.py src/memory/service.py tests/memory/test_view_anchors.py
git commit -m "feat(memory): the view carries each rule's anchors by uid and name

The join is set membership over minted uids in the read path; the service
passes its own anchor store, callers without one get the old shape.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1b: `fade` on the memory view

Requested by the #266 grammar session on 2026-09-04 so the context panel can sort "nearest to fading" first without the host ever learning a half-life. Lands after Task 1's review, before Task 2.

**Files:**
- Modify: `src/memory/constraint.py` (`ConstraintView`)
- Modify: `src/memory/read_api.py` (`get_active_constraints`, `_attach_anchors`)
- Test: `tests/memory/test_view_fade.py` (new)

**Interfaces:**
- Consumes: `HALF_LIFE_DAYS[decay_class] -> int | None` (`memory.models`), `Constraint.last_observed_at`, `Constraint.decay_class`.
- Produces: `ConstraintView.fade: float | None = None`; `fade_on(constraint, day) -> float | None` in `read_api`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_view_fade.py
"""How close a rule is to fading, as a number in [0, 1] the server computes.

The half-life table stays inside the memory server; a host that sorted by
`last_observed_at` would have to know it. `None` means the rule never fades.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from memory.constraint import Constraint, Necessity, Scope, Source, Status
from memory.constraint_store import ConstraintStore
from memory.models import HALF_LIFE_DAYS, DecayClass, Tier
from memory.read_api import fade_on, get_active_constraints

DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


def _rule(name: str, decay_class: DecayClass, *, observed_days_ago: int) -> Constraint:
    return Constraint(
        name=name,
        description=f"{name} description",
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        decay_class=decay_class,
        created_at=NOW - timedelta(days=400),
        last_observed_at=NOW - timedelta(days=observed_days_ago),
    )


def _a_decaying_class() -> DecayClass:
    return next(cls for cls, half in HALF_LIFE_DAYS.items() if half is not None)


def test_a_permanent_rule_never_fades() -> None:
    assert fade_on(_rule("Sleep", DecayClass.PERMANENT, observed_days_ago=900), DAY) is None


def test_fade_is_elapsed_over_half_life_clipped_to_one() -> None:
    cls = _a_decaying_class()
    half = HALF_LIFE_DAYS[cls]
    fresh = _rule("Fresh", cls, observed_days_ago=0)
    halfway = _rule("Halfway", cls, observed_days_ago=half // 2)
    stale = _rule("Stale", cls, observed_days_ago=half * 3)
    assert fade_on(fresh, DAY) == 0.0
    assert abs(fade_on(halfway, DAY) - (half // 2) / half) < 1e-9
    assert fade_on(stale, DAY) == 1.0


def test_active_views_carry_fade(tmp_path) -> None:
    cls = _a_decaying_class()
    store = ConstraintStore(str(tmp_path / "memory.db"))
    store.upsert(_rule("Halfway", cls, observed_days_ago=HALF_LIFE_DAYS[cls] // 2))
    store.upsert(_rule("Sleep", DecayClass.PERMANENT, observed_days_ago=5))

    views = {v.name: v for v in get_active_constraints(store, DAY)}

    assert views["Sleep"].fade is None
    assert 0.0 < views["Halfway"].fade < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/memory/test_view_fade.py -v -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'fade_on'`.

- [ ] **Step 3: The field and the arithmetic**

In `ConstraintView`, after `anchors`:

```python
    #: How close the rule is to fading on the requested day, 0.0 fresh to 1.0
    #: fading tomorrow; None for a rule that never fades. Computed here so the
    #: half-life table never leaves the server; a host sorts on it and learns
    #: nothing about decay.
    fade: float | None = None
```

In `read_api.py`, import `HALF_LIFE_DAYS` from `memory.models` and add:

```python
def fade_on(constraint: Constraint, day: date) -> float | None:
    """Elapsed days since last observation over the half-life, clipped to [0, 1].

    Arithmetic only, the same as `Constraint.has_faded`, which this mirrors:
    a rule `has_faded` exactly when this would exceed 1.0.
    """
    half_life = HALF_LIFE_DAYS[constraint.decay_class]
    if half_life is None:
        return None
    elapsed = (day - constraint.last_observed_at.date()).days
    return min(1.0, max(0.0, elapsed / half_life))
```

In `get_active_constraints`, change the return to attach both:

```python
    ordered = sorted(applicable, key=_reading_order)
    return [
        _attach_anchors(c.to_view(), c.uid, anchors).model_copy(update={"fade": fade_on(c, day)})
        for c in ordered
    ]
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/memory/test_view_fade.py tests/memory/test_view_anchors.py tests/memory/test_decay_read.py -v -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Mutation check**

Make `fade_on` return `0.0` unconditionally: the second and third tests fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/memory/constraint.py src/memory/read_api.py tests/memory/test_view_fade.py
git commit -m "feat(memory): the view carries how close a rule is to fading, computed server-side

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The KG client reads anchors too

**Files:**
- Modify: `src/fateforger/agents/timeboxing/kg_constraint_client.py:58-66, 95-118, 150-179`
- Test: `tests/unit/test_kg_constraint_client.py`

**Interfaces:**
- Consumes: Task 1's `get_active_constraints(..., anchors=)`, `AnchorStore(db_path)`.
- Produces: each row from `query_constraints` carries `"anchors": [{"uid": str, "name": str}, ...]` and `"fade": float | None`; `count_suspended(planned_day: str, day_type: str | None) -> int`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_kg_constraint_client.py`:

```python
from memory.anchor import Anchor
from memory.anchor_store import AnchorStore


async def test_rows_carry_anchor_uid_and_name(tmp_path) -> None:
    """The card groups by anchor name and steers by rule uid, so both travel."""
    rule = _constraint(name="Oats before gym")
    db = _store_with(tmp_path, rule)
    anchors = AnchorStore(db)
    gym = Anchor(name="gym")
    anchors.upsert(gym)
    anchors.replace_constraint_links(rule.uid, [gym.uid])

    [row] = await KGConstraintMemoryClient(db).query_constraints(
        filters={"planned_day": date(2026, 9, 8).isoformat()}
    )

    assert row["anchors"] == [{"uid": gym.uid, "name": "gym"}]
    assert "fade" in row


async def test_suspended_rules_are_counted_not_listed(tmp_path) -> None:
    """On a vacation day every working rule is suspended; the panel shows a count."""
    from memory.constraint import Applicability

    working = _constraint(name="Commute", applicability=Applicability(day_types=["working"]))
    db = _store_with(tmp_path, working)

    client = KGConstraintMemoryClient(db)
    assert await client.count_suspended(date(2026, 9, 9).isoformat(), "vacation") == 1
    assert await client.count_suspended(date(2026, 9, 8).isoformat(), "working") == 0


async def test_an_unanchored_rule_has_an_empty_anchor_list(tmp_path) -> None:
    db = _store_with(tmp_path, _constraint(name="Plan at 17:00"))

    [row] = await KGConstraintMemoryClient(db).query_constraints(
        filters={"planned_day": date(2026, 9, 8).isoformat()}
    )

    assert row["anchors"] == []
```

If the module lacks `pytestmark = pytest.mark.asyncio` and the existing async tests use a decorator, use the same decorator these tests' neighbours use.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_kg_constraint_client.py -v -k anchor`
Expected: FAIL with `KeyError: 'anchors'`.

- [ ] **Step 3: Open the anchor store beside the constraint store**

In `kg_constraint_client.py`, replace `_store` with:

```python
    def _stores(self) -> tuple[Any, Any]:
        """Both stores over the same file, opened per call (see the class note)."""
        from memory.anchor_store import AnchorStore
        from memory.constraint_store import ConstraintStore

        return ConstraintStore(self._db_path), AnchorStore(self._db_path)

    def _store(self) -> Any:
        return self._stores()[0]
```

In `query_constraints`, replace the `get_active_constraints(...)` call:

```python
        constraints, anchors = self._stores()
        views = get_active_constraints(
            constraints,
            day,
            str(stage) if stage else None,
            day_type=filters.get("day_type"),
            anchors=anchors,
        )
```

Add the count method after `query_constraints`, using the read path's existing suspended read (`read_api.get_suspended_constraints(store, day, day_type=)`; check its exact signature at `src/memory/read_api.py:84` and match it):

```python
    async def count_suspended(self, planned_day: str, day_type: str | None) -> int:
        """How many rules memory holds back for this day type. A count, not
        rows: on a vacation day this is every working rule."""
        from memory.read_api import get_suspended_constraints

        day = _as_date(planned_day) or date.today()
        return len(get_suspended_constraints(self._store(), day, day_type=day_type))
```

Check `Applicability(day_types=[...])` is the field name in `memory.constraint`; the test above uses it.

In `_row_from_view`, add two keys after `"frame_slot": view.frame_slot,`:

```python
        "anchors": [{"uid": ref.uid, "name": ref.name} for ref in view.anchors],
        "fade": view.fade,
```

- [ ] **Step 4: Run the client suite**

Run: `.venv/bin/python -m pytest tests/unit/test_kg_constraint_client.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/kg_constraint_client.py tests/unit/test_kg_constraint_client.py
git commit -m "feat(timeboxing): KG constraint rows carry anchors

The client called read_api without an anchor store and could not see the
categories the card groups by.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Contracts: fact kinds, ids, Gate, GateMet, new intents, snapshot fields

**Files:**
- Modify: `src/fateforger/agents/timeboxing/session_contracts.py` (`FactKind` :60-84, `PlannerAssumption` :150-158, snapshot :285-302, intents :392-425, outcomes :426-500, `TurnOutcome` :494)
- Test: `tests/unit/test_timeboxing_session_contracts.py`

**Interfaces:**
- Produces, all in `session_contracts.py`:
  - `FactKind.COVERAGE_MATRIX = "coverage_matrix"`, `FactKind.ELICITED_STATEMENT = "elicited_statement"`, `FactKind.SUSPENDED_CONSTRAINT = "suspended_constraint"`
  - `coverage_fact_id(day: date_type) -> str`, `suspension_fact_id(constraint_uid: str) -> str`, `elicited_fact_id(cell_id: str | None) -> str`
  - `PlannerAssumption.filed_by: Literal["planner", "user"] = "planner"`
  - `CellRef(row: str, criterion: str)` with property `id -> "elicit.{row}.{criterion}"`
  - `Gate(open_cells: list[CellRef], day_label: str, note: str | None = None)`
  - `GateMet(kind="gate_met", gate: Gate)`; `AwaitingUser.gate: Gate | None = None`; `GateMet` in `TurnOutcome`
  - `DenyAssumption(kind="deny_assumption", assumption_id: str)`, `FileAssumption(kind="file_assumption", requirement_id: str, value: JsonValue, why_needed: str)`, `RestoreConstraint(kind="restore_constraint", constraint_uid: str)`; all three in `TimeboxIntent`
  - `PlanningSessionSnapshot.applicable_constraints: list[dict[str, JsonValue]] = []`, `PlanningSessionSnapshot.suspended_constraint_count: int = 0`, `PlanningSessionSnapshot.stage1: Literal["open", "proposed", "closed"] = "open"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_timeboxing_session_contracts.py`:

```python
from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    CellRef,
    DenyAssumption,
    FileAssumption,
    Gate,
    GateMet,
    PlannerAssumption,
    RestoreConstraint,
    coverage_fact_id,
    elicited_fact_id,
    suspension_fact_id,
)


def test_stable_fact_ids_are_minted_from_identifiers_only() -> None:
    assert coverage_fact_id(date(2026, 9, 8)) == "coverage:2026-09-08"
    assert suspension_fact_id("abc123") == "suspend:abc123"
    first, second = elicited_fact_id("elicit.body.unclear"), elicited_fact_id("elicit.body.unclear")
    assert first.startswith("elicited:elicit.body.unclear:")
    assert first != second
    assert elicited_fact_id(None).startswith("elicited:free:")


def test_a_cell_ref_names_its_requirement() -> None:
    assert CellRef(row="body", criterion="unclear").id == "elicit.body.unclear"


def test_gate_met_carries_no_open_cells() -> None:
    gate = Gate(open_cells=[], day_label="working Tuesday")
    assert GateMet(gate=gate).kind == "gate_met"
    assert gate.note is None


def test_an_assumption_is_filed_by_the_planner_unless_said_otherwise() -> None:
    base = dict(assumption_id="a1", requirement_id="elicit.body.unclear", value="x", why_needed="y")
    assert PlannerAssumption(**base).filed_by == "planner"
    assert PlannerAssumption(**base, filed_by="user").filed_by == "user"


def test_new_intents_are_discriminated_by_kind() -> None:
    assert DenyAssumption(assumption_id="a1").kind == "deny_assumption"
    assert FileAssumption(requirement_id="r", value="v", why_needed="w").kind == "file_assumption"
    assert RestoreConstraint(constraint_uid="c1").kind == "restore_constraint"


def test_a_snapshot_without_the_new_fields_still_loads() -> None:
    """Stored sessions predate these fields; the defaults must carry them."""
    from fateforger.agents.timeboxing.session_contracts import PlanningSessionSnapshot

    snapshot = PlanningSessionSnapshot.model_validate(
        {"session_key": "C1:1.0", "revision": 0, "owner_user_id": "U1"}
    )
    assert snapshot.stage1 == "open"
    assert snapshot.applicable_constraints == []
    assert snapshot.suspended_constraint_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_session_contracts.py -v`
Expected: FAIL with `ImportError` on `CellRef`.

- [ ] **Step 3: Fact kinds and id helpers**

In `FactKind`, after `REVISION_INSTRUCTION`:

```python
    #: The Stage 1 coverage matrix: one state per cell, plus the anchor
    #: placement it was classified against. Rewritten whole on every fold under
    #: the stable id `coverage:{day}`, so Back, redo and a restart see one state.
    COVERAGE_MATRIX = "coverage_matrix"
    #: What the user said in answer to a probe, or volunteered in Stage 1, as
    #: ``{"cell": <requirement id or null>, "text": <their words>}``. Never
    #: reused as a request: a request is what they want, this is what holds.
    ELICITED_STATEMENT = "elicited_statement"
    #: A rule the user set aside for this session, ``{"uid": ..., "reason": ...}``
    #: under the id `suspend:{uid}`, so a second "not today" is a no-op and a
    #: restore is deleting one fact. The brief drops the rule; the card shows it
    #: as suspended; memory is untouched.
    SUSPENDED_CONSTRAINT = "suspended_constraint"
```

After the `FactKind` class:

```python
def coverage_fact_id(day: date_type) -> str:
    return f"coverage:{day.isoformat()}"


def suspension_fact_id(constraint_uid: str) -> str:
    return f"suspend:{constraint_uid}"


def elicited_fact_id(cell_id: str | None) -> str:
    return f"elicited:{cell_id or 'free'}:{uuid4()}"
```

- [ ] **Step 4: `filed_by`, `CellRef`, `Gate`**

In `PlannerAssumption`, after `invalidated_by`:

```python
    #: Who supplied the assumption. The planner files one when it must place
    #: something nobody stated; the user files one to force past an open cell.
    #: Both are visible and deniable; only the label differs.
    filed_by: Literal["planner", "user"] = "planner"
```

Directly above `class AwaitingUser`:

```python
class CellRef(_StrictModel):
    """One cell of the Stage 1 coverage matrix: a row and a criterion.

    Both halves are keys this system minted (`elicitation.ROWS`,
    `elicitation.CRITERIA`); the id is the requirement the cell is held as.
    """

    row: str = Field(min_length=1)
    criterion: str = Field(min_length=1)

    @property
    def id(self) -> str:
        return f"elicit.{self.row}.{self.criterion}"


class Gate(_StrictModel):
    """What Stage 1 still needs, typed, so a card renders it without prose.

    `open_cells` is empty exactly when the gate is met. `day_label` is the
    day type and weekday the card names ("working Tuesday"). `note` is the only
    prose and may be absent; nothing branches on it.
    """

    open_cells: list[CellRef] = Field(default_factory=list)
    day_label: str = Field(min_length=1)
    note: str | None = None
```

- [ ] **Step 5: Outcomes and intents**

In `AwaitingUser`, after `options`:

```python
    #: Present only for a Stage 1 probe: the cells still open, the top one
    #: being asked. Absent for every other question the catalog puts.
    gate: Gate | None = None
```

After `class AwaitingUser`:

```python
class GateMet(_StrictModel):
    """Stage 1 proposes to close: nothing is uncovered, and the user decides.

    The renderer offers Next on this outcome and on no other. Consent is the
    user's next message; silence does nothing.
    """

    kind: Literal["gate_met"] = "gate_met"
    gate: Gate
```

Add `GateMet,` to the `TurnOutcome` union after `AwaitingUser,`.

After `class ChooseBlockerOption`:

```python
class FileAssumption(_StrictModel):
    """The user forcing past an open cell: the missing fact supplied as an
    assumption, visible in the card's decided list and deniable there."""

    kind: Literal["file_assumption"] = "file_assumption"
    requirement_id: str = Field(min_length=1)
    value: JsonValue
    why_needed: str = Field(min_length=1)


class DenyAssumption(_StrictModel):
    """Withdraw one assumption, the planner's or the user's, by its minted id.

    The kernel removes it and invalidates what was built on it; the cell it
    answered re-opens. A denial is the user asking to be asked, so re-asking
    that cell is not a violation of ask-once.
    """

    kind: Literal["deny_assumption"] = "deny_assumption"
    assumption_id: str = Field(min_length=1)


class RestoreConstraint(_StrictModel):
    """Undo a "not today": delete the suspension fact for one rule, by uid.

    Restore is deleting one fact; there is no second copy of the rule to
    write back, because the rule never left memory.
    """

    kind: Literal["restore_constraint"] = "restore_constraint"
    constraint_uid: str = Field(min_length=1)
```

Add `FileAssumption,`, `DenyAssumption,` and `RestoreConstraint,` to the `TimeboxIntent` union after `ChooseBlockerOption,`.

- [ ] **Step 6: Snapshot fields**

In `PlanningSessionSnapshot`, after `pending_blocker`:

```python
    #: The active rules the host resolved for this day, written on every
    #: resolve, so the card renders the rows the planner receives, in the
    #: planner's order (#202). Rows are the flat dicts the KG client returns;
    #: nothing here reads their prose. The presence fact stays count-only.
    applicable_constraints: list[dict[str, JsonValue]] = Field(default_factory=list)
    #: How many rules memory holds back for this day type (a vacation day
    #: suspends every working rule). A count because the rows would flood a
    #: card; written by the same resolve that writes the rows.
    suspended_constraint_count: int = Field(default=0, ge=0)
    #: Where Stage 1 stands. `open`: eliciting or not yet evaluated. `proposed`:
    #: the kernel emitted GateMet and is waiting for consent. `closed`: the user
    #: consented, or a Stage 2 fact arrived, and planning may proceed.
    stage1: Literal["open", "proposed", "closed"] = "open"
```

- [ ] **Step 7: Run the contracts suite and the whole unit suite**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_session_contracts.py -v && .venv/bin/python -m pytest tests/unit -q -x`
Expected: all PASS. If a test constructs `PlanningSessionSnapshot` with `extra="forbid"` and a literal dict lacking the new keys, defaults cover it.

- [ ] **Step 8: Commit**

```bash
git add src/fateforger/agents/timeboxing/session_contracts.py tests/unit/test_timeboxing_session_contracts.py
git commit -m "feat(timeboxing): Stage 1 contracts: fact kinds with stable ids, Gate, GateMet, deny and file-assumption intents

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The elicitation module: floor, criteria, matrix, gate

**Files:**
- Create: `src/fateforger/agents/timeboxing/elicitation.py`
- Test: `tests/unit/test_elicitation_gate.py` (new)

**Interfaces:**
- Consumes: Task 3's `CellRef`, `Gate`, `FactKind.COVERAGE_MATRIX`, `coverage_fact_id`, `PlanningSessionSnapshot`, `PlanningDay` (has `.date`, `.day_type` with `.value`).
- Produces: `Concern(key, label, description)`, `Criterion(key, label, question)`, `CONCERNS`, `EXTRA_ROWS`, `ROWS` (all rows, keys), `CRITERIA`, `ALL_CELLS: tuple[CellRef, ...]` (40), `CellState`, `RowStats(rule_count, must_count, stated)`, `CoverageMatrix(cells: dict[str, CellState], placement: dict[str, str], rows: dict[str, RowStats], unaskable: list[str])`, `coverage_matrix(snapshot) -> CoverageMatrix | None`, `ranked_open_cells(matrix) -> list[CellRef]`, `day_label(planning_day) -> str`, `stage1_gate(snapshot) -> Gate`, `row_label(key) -> str`, `criterion_label(key) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_elicitation_gate.py
"""The Stage 1 gate is arithmetic over the snapshot: feed a matrix, read a Gate.

No model is anywhere near this module. A matrix is what a judge wrote into the
snapshot; this decides what it means for the stage, and the same function
answers the kernel and the interpreter so the card and the decision set cannot
disagree about whether Next exists.
"""
from __future__ import annotations

from datetime import date

from fateforger.agents.timeboxing.elicitation import (
    ALL_CELLS,
    CRITERIA,
    ROWS,
    CoverageMatrix,
    RowStats,
    coverage_matrix,
    ranked_open_cells,
    stage1_gate,
)
from fateforger.agents.timeboxing.session_contracts import (
    CellRef,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    coverage_fact_id,
)

DAY = date(2026, 9, 8)  # a Tuesday


def _snapshot(matrix: CoverageMatrix | None) -> PlanningSessionSnapshot:
    facts = []
    if matrix is not None:
        facts.append(
            PlanningFact(
                fact_id=coverage_fact_id(DAY),
                kind=FactKind.COVERAGE_MATRIX,
                value=matrix.model_dump(mode="json"),
                source="system",
            )
        )
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=2,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=DAY, timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=facts,
    )


def _matrix(**states: str) -> CoverageMatrix:
    cells = {cell.id: "not_applicable" for cell in ALL_CELLS}
    cells.update(states)
    return CoverageMatrix(cells=cells)


def test_the_floor_has_eight_rows_and_forty_cells() -> None:
    assert len(ROWS) == 8
    assert len(CRITERIA) == 5
    assert len(ALL_CELLS) == 40
    assert "unplaced" in ROWS and "request" in ROWS


def test_no_matrix_means_nothing_is_open() -> None:
    gate = stage1_gate(_snapshot(None))
    assert gate.open_cells == []
    assert gate.day_label == "working Tuesday"


def test_an_uncovered_cell_holds_the_gate_open() -> None:
    gate = stage1_gate(_snapshot(_matrix(**{"elicit.body.unclear": "uncovered"})))
    assert gate.open_cells == [CellRef(row="body", criterion="unclear")]


def test_covered_and_not_applicable_do_not_hold_it() -> None:
    gate = stage1_gate(
        _snapshot(_matrix(**{"elicit.body.unclear": "covered", "elicit.fixed.unclear": "not_applicable"}))
    )
    assert gate.open_cells == []


def test_ranking_prefers_rows_with_rules_then_musts_then_criterion_order() -> None:
    matrix = _matrix(
        **{
            "elicit.fragile.tacit_knowledge": "uncovered",
            "elicit.body.unclear": "uncovered",
            "elicit.body.tacit_assumptions": "uncovered",
            "elicit.movement.unclear": "uncovered",
        }
    )
    matrix = matrix.model_copy(
        update={
            "rows": {
                "body": RowStats(rule_count=5, must_count=1, stated=0),
                "fragile": RowStats(rule_count=7, must_count=0, stated=1),
                "movement": RowStats(rule_count=0, must_count=0, stated=0),
            }
        }
    )
    ranked = ranked_open_cells(matrix)
    assert [c.id for c in ranked] == [
        "elicit.body.tacit_assumptions",
        "elicit.body.unclear",
        "elicit.fragile.tacit_knowledge",
        "elicit.movement.unclear",
    ]


def test_the_matrix_fact_is_read_back_by_its_stable_id() -> None:
    snapshot = _snapshot(_matrix(**{"elicit.request.unclear": "uncovered"}))
    matrix = coverage_matrix(snapshot)
    assert matrix is not None
    assert matrix.cells["elicit.request.unclear"] == "uncovered"


def test_a_malformed_matrix_fact_is_refused_not_ignored() -> None:
    import pytest

    snapshot = _snapshot(None).model_copy(
        update={
            "facts": [
                PlanningFact(
                    fact_id=coverage_fact_id(DAY),
                    kind=FactKind.COVERAGE_MATRIX,
                    value={"cells": "not a mapping"},
                    source="system",
                )
            ]
        }
    )
    with pytest.raises(ValueError):
        coverage_matrix(snapshot)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_elicitation_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: fateforger.agents.timeboxing.elicitation`.

- [ ] **Step 3: Write the module**

```python
# src/fateforger/agents/timeboxing/elicitation.py
"""The spec Stage 1 reasons against, and the arithmetic gate over it.

Two layers meet here. The concern-floor below is the only authored list in the
Stage 1 design: six concerns at the level of what a day has to have settled,
plus two rows that are not concerns but places a gap can live. Anchors, the
second layer, are minted by the memory server from the user's own words and
never appear here; a judge places them under rows and records the placement in
the matrix fact.

Nothing in this module calls a model. `stage1_gate` reads the matrix a judge
wrote into the snapshot and says what is still open; the kernel and the
interpreter both ask it, so the outcome and the decision set agree about
whether Next exists.

Design: docs/superpowers/specs/2026-09-04-stage1-elicitation-design.md
Measurements behind the criterion wording and the row choice:
docs/superpowers/research/2026-09-04-stage1-spike-findings.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .session_contracts import (
    CellRef,
    FactKind,
    Gate,
    PlanningDay,
    PlanningSessionSnapshot,
    coverage_fact_id,
)


@dataclass(frozen=True, slots=True)
class Concern:
    key: str
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class Criterion:
    key: str
    label: str
    question: str


#: Layer 1. Hugo corrects this before any spike runs (#283); the six
#: planning-meta rules that fit no concern are the known open input.
CONCERNS: tuple[Concern, ...] = (
    Concern("bounded", "how the day is bounded", "when it starts and ends, what frames it"),
    Concern("fixed", "what is fixed", "events, appointments, arrivals that do not move"),
    Concern("movement", "movement and transitions", "commutes, travel, the gaps between fixed things"),
    Concern("body", "body", "food, sleep, energy, exercise; the physical constraints on attention"),
    Concern("fragile", "fragile intentions", "the things that only happen if protected"),
    Concern("not_today", "what today is not", "rules that usually hold and do not today"),
)

#: Not concerns: places a gap can live that no concern covers. `unplaced` holds
#: anchors the placement call could not put under a concern; `request` holds
#: what the user said they want from the day, which the fixture showed carrying
#: the gap every other row was reporting.
EXTRA_ROWS: tuple[Concern, ...] = (
    Concern("unplaced", "rules under no concern", "anchors the placement could not put anywhere"),
    Concern("request", "what you asked for today", "the stated request for this day"),
)

ROWS: dict[str, Concern] = {c.key: c for c in (*CONCERNS, *EXTRA_ROWS)}

#: The five follow-up criteria of Singhal et al., with one discriminator added
#: to `alternatives`: as the paper words it the criterion was uncovered on
#: every row of both spike runs, and a criterion that can never be covered
#: before planning is a gate that never opens.
CRITERIA: tuple[Criterion, ...] = (
    Criterion("tacit_assumptions", "assumptions", "Are the assumptions behind what is on record justified for this day, or unstated?"),
    Criterion("alternatives", "alternatives", "Where a rule here is at risk given what the user said today, has an alternative been considered?"),
    Criterion("unclear", "clarity", "Is anything here ambiguous or underspecified for placing it on today's timeline?"),
    Criterion("contradictory", "contradictions", "Do any statements or rules here contradict each other, or the user's request?"),
    Criterion("tacit_knowledge", "unstated knowledge", "Is there knowledge only the user has, such as durations or arrivals, that is unstated and needed?"),
)

CRITERION_BY_KEY: dict[str, Criterion] = {c.key: c for c in CRITERIA}

ALL_CELLS: tuple[CellRef, ...] = tuple(
    CellRef(row=row, criterion=criterion.key) for row in ROWS for criterion in CRITERIA
)

CellState = Literal["covered", "uncovered", "not_applicable"]


class RowStats(BaseModel):
    """Counts over minted fields a judge records per row when it classifies.

    Ranking reads these and nothing else: no row is marked important by hand.
    """

    model_config = ConfigDict(extra="forbid")

    rule_count: int = Field(ge=0, default=0)
    must_count: int = Field(ge=0, default=0)
    stated: int = Field(ge=0, default=0)


class CoverageMatrix(BaseModel):
    """The Stage 1 coverage state, as stored in the `coverage:{day}` fact."""

    model_config = ConfigDict(extra="forbid")

    cells: dict[str, CellState]
    #: anchor uid -> row key, the placement these cells were classified against
    placement: dict[str, str] = Field(default_factory=dict)
    rows: dict[str, RowStats] = Field(default_factory=dict)
    #: cells the generator could not ground a probe for; still open, shown on
    #: the gate line rather than asked
    unaskable: list[str] = Field(default_factory=list)


def coverage_matrix(snapshot: PlanningSessionSnapshot) -> CoverageMatrix | None:
    """The matrix for the locked day, or None when no judge has written one.

    None is "no elicitation has run", which is the honest state of a session
    before the spikes land; it is not "gate unmet". A fact that exists but
    does not parse is refused: a malformed matrix must not read as an empty one.
    """
    if snapshot.planning_day is None:
        return None
    wanted = coverage_fact_id(snapshot.planning_day.date)
    for fact in snapshot.facts:
        if fact.kind is FactKind.COVERAGE_MATRIX and fact.fact_id == wanted:
            if not isinstance(fact.value, dict):
                raise ValueError(f"coverage matrix fact {wanted} is not an object")
            return CoverageMatrix.model_validate(fact.value)
    return None


def ranked_open_cells(matrix: CoverageMatrix) -> list[CellRef]:
    """Uncovered cells by expected value, every term a count over minted fields.

    A row with rules or stated facts before one with neither; a row carrying a
    `must` before one carrying only `should`s; then the criterion order above.
    """
    order = {c.key: i for i, c in enumerate(CRITERIA)}
    open_cells = [cell for cell in ALL_CELLS if matrix.cells.get(cell.id) == "uncovered"]

    def key(cell: CellRef) -> tuple[int, int, int]:
        stats = matrix.rows.get(cell.row, RowStats())
        has_content = 1 if (stats.rule_count + stats.stated) > 0 else 0
        has_must = 1 if stats.must_count > 0 else 0
        return (-has_content, -has_must, order[cell.criterion])

    return sorted(open_cells, key=key)


def day_label(planning_day: PlanningDay) -> str:
    """"working Tuesday": the day type and the weekday, both minted by the host."""
    return f"{planning_day.day_type.value} {planning_day.date.strftime('%A')}"


def stage1_gate(snapshot: PlanningSessionSnapshot) -> Gate:
    """What Stage 1 still needs. Arithmetic over the snapshot; called by the
    kernel for its outcome and by the interpreter for its decision set."""
    if snapshot.planning_day is None:
        raise ValueError("stage1_gate needs a locked planning day")
    matrix = coverage_matrix(snapshot)
    open_cells = [] if matrix is None else ranked_open_cells(matrix)
    return Gate(open_cells=open_cells, day_label=day_label(snapshot.planning_day))


def row_label(key: str) -> str:
    return ROWS[key].label


def criterion_label(key: str) -> str:
    return CRITERION_BY_KEY[key].label
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/unit/test_elicitation_gate.py -v`
Expected: all PASS.

- [ ] **Step 5: Mutation checks**

Make `ranked_open_cells` return `open_cells` unsorted: the ranking test fails. Make `coverage_matrix` return `None` on a malformed fact: the refusal test fails. Restore both.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/elicitation.py tests/unit/test_elicitation_gate.py
git commit -m "feat(timeboxing): the Stage 1 concern-floor, criteria, coverage matrix and arithmetic gate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Cells as catalog requirements; stage on every requirement

**Files:**
- Modify: `src/fateforger/agents/timeboxing/readiness.py:24-36` (`ArtifactRequirement`), `:120-240` (catalog), `:255-320` (`TimeboxRequirements`)
- Test: `tests/unit/test_timeboxing_readiness.py`

**Interfaces:**
- Consumes: Task 3's `CellRef`, `FactKind.ELICITED_STATEMENT`; Task 4's `ALL_CELLS`, `ROWS`, `CRITERION_BY_KEY`, `coverage_matrix`.
- Produces: `ArtifactRequirement.stage: int` (required), `ArtifactRequirement.cell: CellRef | None = None`; `TimeboxRequirements.stage_of(requirement_id) -> int`; forty requirements `elicit.{row}.{criterion}` in `_REQUIREMENTS`; `_is_satisfied` for a cell reads the matrix.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_timeboxing_readiness.py`:

```python
from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CoverageMatrix
from fateforger.agents.timeboxing.session_contracts import coverage_fact_id


def test_every_requirement_carries_a_stage_and_the_ladder_is_monotone() -> None:
    reqs = TimeboxRequirements()
    assert reqs.stage_of("skeleton.locked_day") == 1
    assert reqs.stage_of("skeleton.day_frame") == 1
    assert reqs.stage_of("skeleton.requested_activity") == 2
    assert reqs.stage_of("skeleton.activity_reading") == 2
    assert reqs.stage_of("candidate.calendar_snapshot") == 4
    assert reqs.stage_of("commit.approved_candidate") == 5


def test_forty_cells_are_soft_user_owned_stage_one_requirements() -> None:
    reqs = TimeboxRequirements()
    report = reqs.evaluate(ArtifactKind.SKELETON, _locked_snapshot())
    cells = [gap for gap in report.gaps if gap.requirement.cell is not None]
    assert len(cells) == 40
    assert all(gap.owner is RequirementOwner.USER and not gap.hard for gap in cells)
    assert all(reqs.stage_of(gap.requirement_id) == 1 for gap in cells)
    assert report.first_hard_user_blocker() is not None  # still day_frame / activity


def test_a_cell_is_satisfied_unless_the_matrix_says_uncovered() -> None:
    reqs = TimeboxRequirements()
    cell = ALL_CELLS[0]
    matrix = CoverageMatrix(cells={c.id: "not_applicable" for c in ALL_CELLS} | {cell.id: "uncovered"})
    snapshot = _locked_snapshot(
        PlanningFact(
            fact_id=coverage_fact_id(date(2026, 8, 29)),
            kind=FactKind.COVERAGE_MATRIX,
            value=matrix.model_dump(mode="json"),
            source="system",
        )
    )
    report = reqs.evaluate(ArtifactKind.SKELETON, snapshot)
    assert report.by_id(cell.id).satisfied is False
    assert report.by_id(ALL_CELLS[1].id).satisfied is True
    assert reqs.evaluate(ArtifactKind.SKELETON, _locked_snapshot()).by_id(cell.id).satisfied is True


def test_an_unknown_requirement_has_no_stage() -> None:
    import pytest

    with pytest.raises(KeyError):
        TimeboxRequirements().stage_of("nothing.like.this")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_readiness.py -v -k "stage or cell"`
Expected: FAIL with `AttributeError: 'TimeboxRequirements' object has no attribute 'stage_of'`.

- [ ] **Step 3: Fields on the requirement**

In `readiness.py`, import `CellRef` from `.session_contracts` and, at the bottom of `ArtifactRequirement`'s fields after `question: str`:

```python
    #: Which of the five stages this requirement's question belongs to. The
    #: card takes its stage from here, so the ladder is a property of the
    #: catalog and not of a map from fact kinds (#276).
    stage: int
    #: Present only for a Stage 1 coverage cell. Satisfaction of a cell is read
    #: from the matrix fact, not from the presence of a statement: one answer
    #: does not satisfy forty questions.
    cell: CellRef | None = None
```

Add `stage=` to every existing entry in `_REQUIREMENTS`: `skeleton.locked_day` 1, `skeleton.requested_activity` 2, `skeleton.day_frame` 1, `skeleton.activity_reading` 2, `skeleton.ordinary_placement` 2, `candidate.approved_skeleton` 3, `candidate.calendar_snapshot` 4, `candidate.active_constraints` 4, `candidate.concrete_placements` 4, `commit.approved_candidate` 5.

- [ ] **Step 4: Generate the cells**

After the `_REQUIREMENTS` tuple, and replacing it as the catalog the class reads:

```python
def _cell_requirements() -> tuple[ArtifactRequirement, ...]:
    """Forty requirements from two fixed lists. Soft, so none is a hard
    blocker; the elicitor picks which to ask, so catalog order means nothing."""
    from .elicitation import ALL_CELLS, CRITERION_BY_KEY, ROWS

    return tuple(
        ArtifactRequirement(
            requirement_id=cell.id,
            target_artifact=ArtifactKind.SKELETON,
            satisfied_by=(FactKind.ELICITED_STATEMENT,),
            owner=RequirementOwner.USER,
            hard=False,
            why_needed=ROWS[cell.row].label,
            resolution="ask",
            question=CRITERION_BY_KEY[cell.criterion].question,
            stage=1,
            cell=cell,
        )
        for cell in ALL_CELLS
    )


_CATALOG: tuple[ArtifactRequirement, ...] = (*_REQUIREMENTS, *_cell_requirements())
```

The local import avoids a cycle: `elicitation` imports `session_contracts`, not `readiness`. Replace every `_REQUIREMENTS` read inside `TimeboxRequirements` (`evaluate`, `target_of`) with `_CATALOG`.

- [ ] **Step 5: `stage_of` and per-cell satisfaction**

In `TimeboxRequirements`, after `target_of`:

```python
    @staticmethod
    def stage_of(requirement_id: str) -> int:
        """The stage a requirement's question belongs to. KeyError for an id
        the catalog does not know: a question with no stage is a defect, not
        a stage-two question."""
        for requirement in _CATALOG:
            if requirement.requirement_id == requirement_id:
                return requirement.stage
        raise KeyError(requirement_id)
```

In `_is_satisfied`, before the final `return all(...)`:

```python
        if requirement.cell is not None:
            from .elicitation import coverage_matrix

            matrix = coverage_matrix(snapshot)
            if matrix is None:
                return True
            return matrix.cells.get(requirement.requirement_id) != "uncovered"
```

- [ ] **Step 6: Run the readiness suite and the kernel suite**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_readiness.py tests/unit/test_adaptive_timeboxing.py -v`
Expected: all PASS. If a kernel test asserts on the exact count of gaps in a `ReadinessReport` for `SKELETON`, it now sees forty more soft gaps; adjust that assertion to filter `gap.requirement.cell is None` and say why in a comment.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/agents/timeboxing/readiness.py tests/unit/test_timeboxing_readiness.py tests/unit/test_adaptive_timeboxing.py
git commit -m "feat(timeboxing): forty Stage 1 cells as soft catalog requirements; every requirement carries its stage

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The kernel: gate, consent, suspensions, deny, file, Back

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` (`_apply_intent` :563-745, run loop :477-535, `_build_brief`, `_go_back` :747-800)
- Test: `tests/unit/test_adaptive_stage1.py` (new)

**Interfaces:**
- Consumes: Task 3's `GateMet`, `Gate`, `DenyAssumption`, `FileAssumption`, `RestoreConstraint`, `PlannerAssumption.filed_by`, snapshot `stage1`, `applicable_constraints`, `suspended_constraint_count`; Task 4's `stage1_gate`; Task 5's `ReadinessReport.by_id`.
- Produces: kernel behaviour, tested below. `_build_brief` drops rows whose `uid` is named by a `SUSPENDED_CONSTRAINT` fact.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_adaptive_stage1.py
"""Stage 1 in the kernel: propose to close, wait for consent, re-open on facts.

The planner is a fake that records the brief it was given, the context port
returns the rows a host would, and no model is anywhere. Every assertion is on
outcomes and snapshot fields this system minted.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    InMemoryPlanningSessionRepository,
    PlanningContext,
    TurnRequest,
)
from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CoverageMatrix
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactDraft,
    ArtifactKind,
    AwaitingUser,
    DayType,
    DenyAssumption,
    FactKind,
    FileAssumption,
    GateMet,
    GoBack,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    RestoreConstraint,
    coverage_fact_id,
    elicited_fact_id,
    suspension_fact_id,
)

DAY = date(2026, 9, 8)
ROWS = [
    {"uid": "c-gym", "name": "Oats before gym", "necessity": "must", "anchors": [{"uid": "a1", "name": "gym"}]},
    {"uid": "c-plan", "name": "Plan at 17:00", "necessity": "should", "anchors": []},
]


class _Planner:
    def __init__(self) -> None:
        self.briefs = []

    async def produce(self, brief, progress):
        self.briefs.append(brief)
        return PlanningResult(
            artifact_updates=[
                ArtifactDraft(
                    kind=ArtifactKind.SKELETON,
                    payload={"markdown": "## Tuesday"},
                    dependency_revisions={"planning_day": 1},
                )
            ]
        )


class _Context:
    async def propose_planning_day(self, request):
        raise AssertionError("day is locked in these tests")

    async def resolve(self, snapshot, *, target, progress):
        return PlanningContext(applicable_constraints=ROWS, suspended_constraint_count=3)


class _Commit:
    async def commit(self, candidate, *, digest):
        raise AssertionError("no commit in Stage 1")


class _Sink:
    async def emit(self, event):
        return None


def _snapshot(**update) -> PlanningSessionSnapshot:
    base = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=DAY, timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=[
            PlanningFact(fact_id="activity-1", kind=FactKind.REQUESTED_ACTIVITY, value="deep work", source="user"),
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:30"}, source="user"),
        ],
    )
    return base.model_copy(update=update)


def _kernel(snapshot: PlanningSessionSnapshot):
    repository = InMemoryPlanningSessionRepository([snapshot])
    planner = _Planner()
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=_Context(),
        commit=_Commit(),
    )
    return kernel, repository, planner


def _turn(kernel, snapshot, intent):
    return asyncio.run(
        kernel.turn(
            TurnRequest(
                session_key=snapshot.session_key,
                interaction_id="1.1",
                actor_user_id="U1",
                expected_revision=snapshot.revision,
                intent=intent,
            ),
            progress=_Sink(),
        )
    )


def _load(repository, key="C1:1.0"):
    return asyncio.run(repository.load_or_create(key, owner_user_id="U1"))


def _matrix_fact(open_cell_id: str | None):
    cells = {c.id: "not_applicable" for c in ALL_CELLS}
    if open_cell_id:
        cells[open_cell_id] = "uncovered"
    return PlanningFact(
        fact_id=coverage_fact_id(DAY),
        kind=FactKind.COVERAGE_MATRIX,
        value=CoverageMatrix(cells=cells).model_dump(mode="json"),
        source="system",
    )


def test_a_locked_day_with_no_open_cells_proposes_to_close_and_plans_nothing() -> None:
    kernel, repository, planner = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), Advance())
    assert isinstance(outcome, GateMet)
    assert outcome.gate.open_cells == []
    assert outcome.gate.day_label == "working Tuesday"
    assert planner.briefs == []
    current = _load(repository)
    assert current.stage1 == "proposed"
    assert [row["uid"] for row in current.applicable_constraints] == ["c-gym", "c-plan"]
    assert current.suspended_constraint_count == 3


def test_consent_is_the_next_advance_and_then_the_planner_runs() -> None:
    kernel, repository, planner = _kernel(_snapshot(stage1="proposed"))
    _turn(kernel, _snapshot(stage1="proposed"), Advance())
    assert _load(repository).stage1 == "closed"
    assert len(planner.briefs) == 1


def test_an_open_cell_is_asked_with_the_gate_attached() -> None:
    cell = ALL_CELLS[0]
    snapshot = _snapshot(facts=[*_snapshot().facts, _matrix_fact(cell.id)])
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.gate is not None and [c.id for c in outcome.gate.open_cells] == [cell.id]
    assert _load(repository).pending_blocker.requirement_id == cell.id
    assert planner.briefs == []


def test_an_elicited_statement_after_a_proposal_re_opens_the_stage() -> None:
    kernel, repository, _ = _kernel(_snapshot(stage1="proposed"))
    fact = PlanningFact(
        fact_id=elicited_fact_id(None), kind=FactKind.ELICITED_STATEMENT,
        value={"cell": None, "text": "dentist at 15:00"}, source="user",
    )
    outcome = _turn(kernel, _snapshot(stage1="proposed"), ProvidePlanningFacts(facts=[fact]))
    assert isinstance(outcome, GateMet)  # no judge yet, so nothing is open; but the stage was re-evaluated
    assert _load(repository).stage1 == "proposed"


def test_a_stage_two_fact_after_a_proposal_is_consent() -> None:
    kernel, repository, planner = _kernel(_snapshot(stage1="proposed"))
    fact = PlanningFact(fact_id="activity-2", kind=FactKind.REQUESTED_ACTIVITY, value="gym at 18:00", source="user")
    _turn(kernel, _snapshot(stage1="proposed"), ProvidePlanningFacts(facts=[fact]))
    assert _load(repository).stage1 == "closed"
    assert len(planner.briefs) == 1


def test_a_suspended_rule_reaches_the_card_but_not_the_brief() -> None:
    kernel, repository, planner = _kernel(_snapshot(stage1="proposed"))
    suspend = PlanningFact(
        fact_id=suspension_fact_id("c-gym"), kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    _turn(kernel, _snapshot(stage1="proposed"), ProvidePlanningFacts(facts=[suspend]))
    reopened = _load(repository)
    assert reopened.stage1 == "proposed"
    assert [row["uid"] for row in reopened.applicable_constraints] == ["c-gym", "c-plan"]
    _turn(kernel, reopened, Advance())
    [brief] = planner.briefs
    assert [row["uid"] for row in brief.applicable_constraints] == ["c-plan"]


def test_restore_deletes_the_suspension_and_reopens_the_stage() -> None:
    suspend = PlanningFact(
        fact_id=suspension_fact_id("c-gym"), kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    snapshot = _snapshot(stage1="proposed", facts=[*_snapshot().facts, suspend])
    kernel, repository, _ = _kernel(snapshot)
    _turn(kernel, snapshot, RestoreConstraint(constraint_uid="c-gym"))
    after = _load(repository)
    assert not any(f.kind is FactKind.SUSPENDED_CONSTRAINT for f in after.facts)
    assert after.stage1 == "proposed"  # re-evaluated in the same turn; nothing open, proposed again


def test_restore_of_a_rule_not_suspended_is_refused() -> None:
    from fateforger.agents.timeboxing.session_contracts import TurnFailed

    kernel, _, _ = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), RestoreConstraint(constraint_uid="c-gym"))
    assert isinstance(outcome, TurnFailed) and outcome.code == "stale_restore"


def test_file_assumption_is_recorded_as_the_users_and_closes_the_question() -> None:
    cell = ALL_CELLS[0]
    snapshot = _snapshot(facts=[*_snapshot().facts, _matrix_fact(cell.id)])
    kernel, repository, _ = _kernel(snapshot)
    _turn(kernel, snapshot, Advance())
    held = _load(repository)
    _turn(kernel, held, FileAssumption(requirement_id=cell.id, value="assume a normal day", why_needed="user forced past"))
    after = _load(repository)
    [assumption] = after.assumptions
    assert assumption.filed_by == "user" and assumption.requirement_id == cell.id
    assert after.pending_blocker is None


def test_deny_removes_the_assumption_and_reopens_the_stage() -> None:
    cell = ALL_CELLS[0]
    snapshot = _snapshot(facts=[*_snapshot().facts, _matrix_fact(cell.id)])
    kernel, repository, _ = _kernel(snapshot)
    _turn(kernel, snapshot, Advance())
    _turn(kernel, _load(repository), FileAssumption(requirement_id=cell.id, value="x", why_needed="y"))
    [assumption] = _load(repository).assumptions
    _turn(kernel, _load(repository), DenyAssumption(assumption_id=assumption.assumption_id))
    after = _load(repository)
    assert after.assumptions == []
    assert after.stage1 == "open"


def test_deny_of_an_unknown_assumption_is_refused() -> None:
    from fateforger.agents.timeboxing.session_contracts import TurnFailed

    kernel, _, _ = _kernel(_snapshot())
    outcome = _turn(kernel, _snapshot(), DenyAssumption(assumption_id="nope"))
    assert isinstance(outcome, TurnFailed) and outcome.code == "stale_assumption"


def test_back_from_a_proposal_reopens_stage_one() -> None:
    kernel, repository, _ = _kernel(_snapshot(stage1="proposed"))
    _turn(kernel, _snapshot(stage1="proposed"), GoBack())
    assert _load(repository).stage1 == "open"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_adaptive_stage1.py -v`
Expected: the first test FAILS because the outcome is `ArtifactReady`/`AwaitingApproval` (the planner ran) rather than `GateMet`.

- [ ] **Step 3: Intents in `_apply_intent`**

Import `DenyAssumption`, `FileAssumption`, `GateMet`, `PlannerAssumption` in the module's `session_contracts` import block, and `stage1_gate` from `.elicitation`.

Replace the `StartSession`/`Advance` branch:

```python
        if isinstance(intent, StartSession):
            return snapshot, None
        if isinstance(intent, Advance):
            if snapshot.stage1 == "proposed":
                # Consent: the user saw the proposal and said go on.
                return snapshot.model_copy(update={"stage1": "closed"}), None
            return snapshot, None
```

Replace the `ProvidePlanningFacts` branch:

```python
        if isinstance(intent, ProvidePlanningFacts):
            merged = self._merge_facts(snapshot, intent.facts)
            if merged.facts == snapshot.facts:
                return snapshot, None
            stage1_kinds = {FactKind.ELICITED_STATEMENT, FactKind.SUSPENDED_CONSTRAINT}
            if any(fact.kind in stage1_kinds for fact in intent.facts):
                # A Stage 1 fact re-enters the loop: a new fact can uncover a cell.
                merged = merged.model_copy(update={"stage1": "open"})
            elif merged.stage1 == "proposed":
                # Anything else after a proposal is consent, and the message is
                # handled in Stage 2, so "let's plan, deep work first" is one turn.
                merged = merged.model_copy(update={"stage1": "closed"})
            return self._reopen(
                self._invalidate(merged, ArtifactKind.CAPTURED_INPUTS)
            ), None
```

Before the `GoBack` branch:

```python
        if isinstance(intent, FileAssumption):
            filed = PlannerAssumption(
                assumption_id=str(uuid4()),
                requirement_id=intent.requirement_id,
                value=intent.value,
                why_needed=intent.why_needed,
                filed_by="user",
            )
            pending = snapshot.pending_blocker
            updated = snapshot.model_copy(
                update={
                    "assumptions": [*snapshot.assumptions, filed],
                    "pending_blocker": None
                    if pending is not None and pending.requirement_id == intent.requirement_id
                    else pending,
                }
            )
            return self._invalidate(updated, ArtifactKind.CAPTURED_INPUTS), None
        if isinstance(intent, DenyAssumption):
            kept = [a for a in snapshot.assumptions if a.assumption_id != intent.assumption_id]
            if len(kept) == len(snapshot.assumptions):
                return snapshot, TurnFailed(
                    code="stale_assumption",
                    message="That assumption is no longer on record.",
                )
            # The cell it answered re-opens; a denial is the user asking to be asked.
            updated = snapshot.model_copy(update={"assumptions": kept, "stage1": "open"})
            return self._invalidate(updated, ArtifactKind.CAPTURED_INPUTS), None
        if isinstance(intent, RestoreConstraint):
            wanted = suspension_fact_id(intent.constraint_uid)
            kept_facts = [f for f in snapshot.facts if f.fact_id != wanted]
            if len(kept_facts) == len(snapshot.facts):
                return snapshot, TurnFailed(
                    code="stale_restore",
                    message="That rule is not set aside for this session.",
                )
            # A restored rule can uncover a cell, same as a new fact.
            updated = snapshot.model_copy(update={"facts": kept_facts, "stage1": "open"})
            return self._invalidate(updated, ArtifactKind.CAPTURED_INPUTS), None
```

Import `RestoreConstraint` and `suspension_fact_id` from `.session_contracts`.

- [ ] **Step 4: The gate in the run loop**

After `snapshot = self._merge_facts(snapshot, resolved.facts)` and before `readiness = self._requirements.evaluate(...)`:

```python
        rows = resolved.applicable_constraints
        if isinstance(rows, list):
            # Written on every resolve: the read is model-free and the set can
            # change mid-session. The presence fact stays count-only.
            snapshot = snapshot.model_copy(
                update={
                    "applicable_constraints": rows,
                    "suspended_constraint_count": resolved.suspended_constraint_count,
                }
            )
```

`PlanningContext` (adaptive_timeboxing.py:73-83) gains `suspended_constraint_count: int = Field(default=0, ge=0)` beside `applicable_constraints`.

After the `first_hard_user_blocker` block and before `if readiness.system_owned_gaps():`:

```python
        if target is ArtifactKind.SKELETON and snapshot.stage1 != "closed":
            gate = stage1_gate(snapshot)
            if gate.open_cells:
                top = gate.open_cells[0]
                gap = readiness.by_id(top.id)
                return await self._save(
                    self._hold_question(snapshot, gap, []),
                    base_revision=base_revision,
                    request=request,
                    outcome=AwaitingUser(
                        requirement_id=gap.requirement_id,
                        question=gap.question,
                        why_needed=gap.why_needed,
                        gate=gate,
                    ),
                )
            return await self._save(
                snapshot.model_copy(update={"stage1": "proposed"}),
                base_revision=base_revision,
                request=request,
                outcome=GateMet(gate=gate),
            )
```

- [ ] **Step 5: The brief drops suspended rows**

In `_build_brief`, where `applicable_constraints=context.applicable_constraints` is passed (line ~1214), replace the value with `self._unsuspended(snapshot, context.applicable_constraints)` and add:

```python
    @staticmethod
    def _unsuspended(snapshot: PlanningSessionSnapshot, rows: JsonValue) -> JsonValue:
        """Rows minus the ones a SUSPENDED_CONSTRAINT fact names, by uid.

        Set membership over identifiers this system minted. The snapshot keeps
        the full list so the card can show a suspended row as suspended; only
        the planner stops seeing it.
        """
        if not isinstance(rows, list):
            return rows
        suspended = {
            fact.value["uid"]
            for fact in snapshot.facts
            if fact.kind is FactKind.SUSPENDED_CONSTRAINT and isinstance(fact.value, dict)
        }
        return [row for row in rows if not (isinstance(row, dict) and row.get("uid") in suspended)]
```

- [ ] **Step 6: Back re-opens Stage 1**

In `_go_back`, after the `status != "open"` guard and before the first artifact rung, add:

```python
        if (
            self._latest_artifact(snapshot, ArtifactKind.SKELETON) is None
            and snapshot.stage1 != "open"
        ):
            return snapshot.model_copy(update={"stage1": "open", "pending_blocker": None}), None
```

- [ ] **Step 7: Run the new suite and every kernel test**

Run: `.venv/bin/python -m pytest tests/unit/test_adaptive_stage1.py tests/unit/test_adaptive_timeboxing.py tests/unit/test_adaptive_turn_marks_timeboxing_active.py tests/unit/test_timeboxing_intents.py -v`
Expected: the new suite passes. Existing kernel tests that drove `Advance` on a locked day straight into the planner now receive `GateMet` first. For each such test, seed the snapshot with `stage1="closed"` and add the comment `# Stage 1 consent is given; this test is about what follows.` A test named for Stage 1 behaviour itself should assert the new outcome instead.

- [ ] **Step 8: Mutation checks**

Make the gate block emit `GateMet` even when `open_cells` is non-empty: the open-cell test fails. Make `_unsuspended` return `rows`: the suspension test fails. Restore.

- [ ] **Step 9: Commit**

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/unit/test_adaptive_stage1.py tests/unit/test_adaptive_timeboxing.py tests/unit/test_timeboxing_intents.py
git commit -m "feat(timeboxing): Stage 1 proposes to close and waits for consent; suspensions drop from the brief; deny and file-assumption intents

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The host hands Stage 1 the rules it already fetched

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_host.py:201-236` (`_frame_from_corpus`)
- Test: `tests/unit/test_timeboxing_host_stage1_rows.py` (new)

**Interfaces:**
- Consumes: `HostPlanningContext.resolve(snapshot, target=ArtifactKind.SKELETON, progress=)`, `PlanningContext.applicable_constraints`.
- Produces: `resolve(SKELETON)` returns `PlanningContext(applicable_constraints=<rows>, suspended_constraint_count=<n>)` whether or not a frame fact is derived; the count comes from Task 2's `count_suspended`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_timeboxing_host_stage1_rows.py
"""resolve(SKELETON) used to fetch the active rules for the day-frame judgement
and return them to nobody; Stage 1 rendered nothing because it received
nothing (#262)."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_host import HostPlanningContext

ROWS = [{"uid": "c1", "name": "Oats before gym", "necessity": "must", "anchors": []}]


class _Store:
    async def query_constraints(self, *, filters, limit):
        return ROWS

    async def count_suspended(self, planned_day, day_type):
        return 7


class _Sink:
    async def emit(self, event):
        return None


def _snapshot(*facts) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=list(facts),
    )


def test_skeleton_context_carries_the_rows_when_the_frame_is_already_stated() -> None:
    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=object())
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00"}, source="user")

    context = asyncio.run(host.resolve(_snapshot(frame), target=ArtifactKind.SKELETON, progress=_Sink()))

    assert context.applicable_constraints == ROWS
    assert context.suspended_constraint_count == 7
    assert context.facts == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_host_stage1_rows.py -v`
Expected: FAIL, `applicable_constraints == {}`.

- [ ] **Step 3: Return the rows in both branches**

Rewrite `_frame_from_corpus`:

```python
    async def _frame_from_corpus(
        self, snapshot: PlanningSessionSnapshot
    ) -> PlanningContext:
        """What memory says about the day: the active rules, and the sleep
        window as a fact when the user has not stated one.

        The rules are returned in every case. Until 2026-09-04 they were
        fetched here only to feed the frame judgement and handed to nobody, so
        Stage 1 had nothing to show (#262). The frame judgement is unchanged:
        skipped when the user typed a frame this session, a model call
        otherwise, and a host that cannot judge fails the turn rather than
        asking the user every day forever.
        """
        planning_day = self._locked_day(snapshot)
        constraints = await self._active_constraints(planning_day)
        suspended = await self._suspended_count(planning_day)
        if any(fact.kind is FactKind.DAY_FRAME for fact in snapshot.facts):
            return PlanningContext(
                applicable_constraints=constraints, suspended_constraint_count=suspended
            )
        model_client = getattr(self._runtime, "timeboxing_intent_model_client", None)
        if model_client is None:
            raise AdaptiveDependencyUnavailable("no model client for the frame judgement")

        from fateforger.agents.timeboxing.day_frame import DayFrameJudge

        frame = await DayFrameJudge(model_client).frame_on_record(
            day=planning_day,
            constraints=constraints,
            session_key=snapshot.session_key,
        )
        return PlanningContext(
            facts=[] if frame is None else [frame],
            applicable_constraints=constraints,
            suspended_constraint_count=suspended,
        )

    async def _suspended_count(self, planning_day: PlanningDay) -> int:
        store = getattr(self._runtime, "timeboxing_constraint_store", None)
        if store is None:
            raise AdaptiveDependencyUnavailable("constraint memory is unavailable")
        return int(
            await store.count_suspended(
                planning_day.date.isoformat(), planning_day.day_type.value
            )
        )
```

`timeboxing_constraint_store` is the `ClientBackedDurableConstraintStore` wrapping the KG client (`durable_constraint_store.py:799`); if it does not forward `count_suspended` to the client, add a one-line passthrough there with a test beside the adapter's existing tests. `UnavailableConstraintReader` must raise the same `AdaptiveDependencyUnavailable` shape it does for `query_constraints`.

- [ ] **Step 4: Run the host tests**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_host_stage1_rows.py tests/unit/test_day_frame_on_record.py -v && .venv/bin/python -m pytest tests/unit -q -k "host or frame"`
Expected: all PASS. One behavioural change to note in the commit: the rules are now fetched even when a frame is on record, which the old early return skipped.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_host.py tests/unit/test_timeboxing_host_stage1_rows.py
git commit -m "fix(timeboxing): resolve(SKELETON) hands Stage 1 the active rules it already fetched (#262)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The Stage 1 decision set on the surface

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_intents.py` (`InterpretedTimeboxTurn` :80-113, `PlanningFactDraft` :58-70, `ArtifactActionMeta` :114-146, `_TIMEBOX_PROMPT_FRAGMENT` :150-175, `_display_context` :231-278, `_intent_from_interpreted` :352-411, `_typed_facts` :413-424, `intent_from_artifact_action` :427+)
- Test: `tests/unit/test_timeboxing_intents.py`

**Interfaces:**
- Consumes: Task 3's `DenyAssumption`, `FileAssumption`, `FactKind.ELICITED_STATEMENT`/`SUSPENDED_CONSTRAINT`, `elicited_fact_id`, `suspension_fact_id`, snapshot `stage1`, `applicable_constraints`; Task 4's `stage1_gate`.
- Produces: decisions `steer_not_today`, `restore`, `assume`, `deny` on `InterpretedTimeboxTurn` with `constraint_uid: str | None` and `assumption_id: str | None`; `restore` is offered only while a suspension fact exists; `ElicitedStatementDraft(kind="elicited_statement", value: str)`; `ArtifactActionMeta.decision` gains `"deny_assumption"` and a validator requiring `assumption_id`; `_display_context` offers Stage 1's set and `advance` only when `stage1 == "proposed"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_timeboxing_intents.py`:

```python
from fateforger.agents.timeboxing.session_contracts import (
    DenyAssumption,
    FileAssumption,
    PlannerAssumption,
    PlanningFact,
    ProvidePlanningFacts,
    RestoreConstraint,
)
from fateforger.slack_bot.timeboxing_intents import _display_context


def _stage1_snapshot(**update) -> PlanningSessionSnapshot:
    return _capture_snapshot().model_copy(
        update={
            "applicable_constraints": [{"uid": "c-gym", "name": "Oats before gym", "necessity": "must", "anchors": []}],
            **update,
        }
    )


def test_stage_one_offers_next_only_after_the_kernel_proposed() -> None:
    _, open_decisions, _ = _display_context(_stage1_snapshot(stage1="open"))
    _, proposed_decisions, _ = _display_context(_stage1_snapshot(stage1="proposed"))
    assert "advance" not in open_decisions
    assert "advance" in proposed_decisions
    for decisions in (open_decisions, proposed_decisions):
        assert {"provide_facts", "steer_not_today", "assume", "deny", "back", "cancel"} <= set(decisions)
        assert "steer_always" not in decisions  # not honourable until its flow lands
        assert "restore" not in decisions  # nothing is suspended


def test_restore_is_offered_only_while_something_is_suspended() -> None:
    suspend = PlanningFact(
        fact_id="suspend:c-gym", kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    _, decisions, _ = _display_context(_stage1_snapshot(facts=[suspend]))
    assert "restore" in decisions


async def test_restore_names_a_suspended_rule() -> None:
    suspend = PlanningFact(
        fact_id="suspend:c-gym", kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    client = _SchemaOutputClient({"decision": "restore", "constraint_uid": "c-gym"})
    intent = await TimeboxingIntentInterpreter(client).interpret("put the oats rule back", _stage1_snapshot(facts=[suspend]))
    assert intent == RestoreConstraint(constraint_uid="c-gym")


async def test_not_today_names_a_rule_the_snapshot_holds() -> None:
    client = _SchemaOutputClient({"decision": "steer_not_today", "constraint_uid": "c-gym"})
    intent = await TimeboxingIntentInterpreter(client).interpret("skip the oats thing today", _stage1_snapshot())
    assert isinstance(intent, ProvidePlanningFacts)
    [fact] = intent.facts
    assert fact.kind is FactKind.SUSPENDED_CONSTRAINT
    assert fact.fact_id == "suspend:c-gym"
    assert fact.value == {"uid": "c-gym", "reason": "not today"}


async def test_not_today_for_a_rule_not_on_the_card_is_refused() -> None:
    client = _SchemaOutputClient({"decision": "steer_not_today", "constraint_uid": "c-other"})
    with pytest.raises(ValueError, match="not among"):
        await TimeboxingIntentInterpreter(client).interpret("skip it", _stage1_snapshot())


async def test_assume_files_against_the_open_cell() -> None:
    pending = PendingBlocker(requirement_id="elicit.body.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    client = _SchemaOutputClient({"decision": "assume"})
    intent = await TimeboxingIntentInterpreter(client).interpret("just assume a normal day", _stage1_snapshot(pending_blocker=pending))
    assert isinstance(intent, FileAssumption) and intent.requirement_id == "elicit.body.unclear"


async def test_assume_with_nothing_open_is_refused() -> None:
    client = _SchemaOutputClient({"decision": "assume"})
    with pytest.raises(ValueError, match="no open"):
        await TimeboxingIntentInterpreter(client).interpret("just assume", _stage1_snapshot())


async def test_deny_names_an_assumption_on_record() -> None:
    filed = PlannerAssumption(assumption_id="a1", requirement_id="elicit.body.unclear", value="x", why_needed="y", filed_by="user")
    client = _SchemaOutputClient({"decision": "deny", "assumption_id": "a1"})
    intent = await TimeboxingIntentInterpreter(client).interpret("no, don't assume that", _stage1_snapshot(assumptions=[filed]))
    assert intent == DenyAssumption(assumption_id="a1")


async def test_an_answer_to_a_probe_is_an_elicited_statement_for_that_cell() -> None:
    pending = PendingBlocker(requirement_id="elicit.fixed.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    client = _SchemaOutputClient(
        {"decision": "provide_facts", "facts": [{"kind": "elicited_statement", "value": "dentist at 15:00, fixed"}]}
    )
    intent = await TimeboxingIntentInterpreter(client).interpret("dentist at 15:00, fixed", _stage1_snapshot(pending_blocker=pending))
    [fact] = intent.facts
    assert fact.kind is FactKind.ELICITED_STATEMENT
    assert fact.value == {"cell": "elicit.fixed.unclear", "text": "dentist at 15:00, fixed"}
    assert fact.fact_id.startswith("elicited:elicit.fixed.unclear:")


def test_a_deny_button_press_binds_to_its_assumption() -> None:
    envelope = intent_from_artifact_action(
        {"session_key": "C1:1.0", "expected_revision": 3, "decision": "deny_assumption", "assumption_id": "a1"}
    )
    assert envelope is not None and envelope.intent == DenyAssumption(assumption_id="a1")
    assert intent_from_artifact_action({"session_key": "C1:1.0", "expected_revision": 3, "decision": "deny_assumption"}) is None
```

The existing `_SchemaOutputClient` returns exactly the dict given; the interpreter validates it against the narrowed schema, so a decision the surface did not offer raises. Check the existing tests for whether the module is `asyncio`-marked and mirror them.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_intents.py -v -k "stage_one or not_today or assume or deny or elicited"`
Expected: FAIL with `ImportError` / `ValidationError` on unknown decisions.

- [ ] **Step 3: Schema**

Add a draft and extend the union:

```python
class ElicitedStatementDraft(_StrictModel):
    """What the user said in Stage 1, in their words. The cell it answers is
    the open question's, bound by the host, never named by the model."""

    kind: Literal["elicited_statement"]
    value: str = Field(min_length=1)


PlanningFactDraft = Annotated[
    RequestedActivityDraft | DayFrameFactDraft | ElicitedStatementDraft,
    Field(discriminator="kind"),
]
```

In `InterpretedTimeboxTurn.decision`, add `"steer_not_today", "restore", "assume", "deny"` to the `Literal`, and after `day_offset`:

```python
    #: Which rule to set aside for this session, by the uid the card offered.
    #: The host checks it is on the card; an id the model invents is refused.
    constraint_uid: str | None = None
    #: Which assumption to withdraw, by the id the card offered.
    assumption_id: str | None = None
```

In `ArtifactActionMeta.decision`, add `"deny_assumption"` and `"restore"`; add `assumption_id: str | None = Field(default=None, min_length=1)` and `constraint_uid: str | None = Field(default=None, min_length=1)`; in the validator add:

```python
        if self.decision == "deny_assumption" and self.assumption_id is None:
            raise ValueError("denying an assumption requires its id")
        if self.decision == "restore" and self.constraint_uid is None:
            raise ValueError("restoring a rule requires its uid")
```

- [ ] **Step 4: Prompt fragment**

Append to `_TIMEBOX_PROMPT_FRAGMENT`:

```
When the surface offers steer_not_today, the user is setting one rule from
the card aside for this session; answer with that rule's constraint_uid from
the card, never a name. When it offers restore, the user is putting a rule
they set aside back; answer with that rule's constraint_uid. When it offers assume, the user is telling you to move
on without the answer to the open question; nothing else is needed. When it
offers deny, the user is withdrawing an assumption shown on the card; answer
with its assumption_id. A reply to an open elicit.* question, or anything
the user states about what holds today that is not a request for the day, is
an elicited_statement fact in their words.
```

- [ ] **Step 5: `_display_context`**

Replace the `capture` branch:

```python
    if _latest_artifact(snapshot, ArtifactKind.SKELETON) is None:
        # Stage 1. Next is offered only after the kernel proposed to close, and
        # steer_always is absent until its ask-first flow lands: a decision the
        # session cannot honour is one the model can only waste a turn on.
        consent = ("advance",) if snapshot.stage1 == "proposed" else ()
        restore = (
            ("restore",)
            if any(fact.kind is FactKind.SUSPENDED_CONSTRAINT for fact in snapshot.facts)
            else ()
        )
        return (
            "capture",
            ("provide_facts", "steer_not_today", *restore, "assume", "deny", *consent, *choose, "back", "cancel"),
            None,
        )
```

- [ ] **Step 6: Binding**

In `_intent_from_interpreted`, before `if interpreted.decision == "advance":`:

```python
    if interpreted.decision == "steer_not_today":
        offered = {row.get("uid") for row in snapshot.applicable_constraints if isinstance(row, dict)}
        if interpreted.constraint_uid is None or interpreted.constraint_uid not in offered:
            raise ValueError("steer_not_today names a rule not among the card's rows")
        return ProvidePlanningFacts(
            facts=[
                PlanningFact(
                    fact_id=suspension_fact_id(interpreted.constraint_uid),
                    kind=FactKind.SUSPENDED_CONSTRAINT,
                    value={"uid": interpreted.constraint_uid, "reason": "not today"},
                    source="user",
                )
            ]
        )
    if interpreted.decision == "restore":
        suspended = {
            fact.value["uid"]
            for fact in snapshot.facts
            if fact.kind is FactKind.SUSPENDED_CONSTRAINT and isinstance(fact.value, dict)
        }
        if interpreted.constraint_uid is None or interpreted.constraint_uid not in suspended:
            raise ValueError("restore names a rule that is not set aside")
        return RestoreConstraint(constraint_uid=interpreted.constraint_uid)
    if interpreted.decision == "assume":
        open_question = snapshot.pending_blocker
        if open_question is None:
            raise ValueError("assume requires no open question to be pending; there is no open question")
        return FileAssumption(
            requirement_id=open_question.requirement_id,
            value="assumed by the user",
            why_needed="the user chose to move on without answering",
        )
    if interpreted.decision == "deny":
        known = {a.assumption_id for a in snapshot.assumptions}
        if interpreted.assumption_id is None or interpreted.assumption_id not in known:
            raise ValueError("deny names an assumption not on record")
        return DenyAssumption(assumption_id=interpreted.assumption_id)
```

Fix the `assume` error text to read `"assume requires an open question; there is no open question"` so the test's `match="no open"` holds.

Replace `_typed_facts` so an elicited statement binds to the open cell:

```python
def _typed_facts(
    interpreted: InterpretedTimeboxTurn, snapshot: PlanningSessionSnapshot
) -> list[PlanningFact]:
    pending = snapshot.pending_blocker
    open_cell = (
        pending.requirement_id
        if pending is not None and pending.fact_kind is FactKind.ELICITED_STATEMENT
        else None
    )
    facts: list[PlanningFact] = []
    for fact in interpreted.facts:
        if isinstance(fact, ElicitedStatementDraft):
            facts.append(
                PlanningFact(
                    fact_id=elicited_fact_id(open_cell),
                    kind=FactKind.ELICITED_STATEMENT,
                    value={"cell": open_cell, "text": fact.value},
                    source="user",
                )
            )
            continue
        facts.append(
            PlanningFact(
                fact_id=str(uuid4()),
                kind=FactKind(fact.kind),
                value=fact.value.as_value() if isinstance(fact.value, DayFrameDraft) else fact.value,
                source="user",
            )
        )
    return facts
```

Update both call sites to `_typed_facts(interpreted, snapshot)`. In `intent_from_artifact_action`, add:

```python
    elif meta.decision == "deny_assumption":
        intent = DenyAssumption(assumption_id=cast(str, meta.assumption_id))
    elif meta.decision == "restore":
        intent = RestoreConstraint(constraint_uid=cast(str, meta.constraint_uid))
```

- [ ] **Step 7: Run the interpreter suite**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_intents.py -v`
Expected: all PASS. An existing test asserting the exact `capture` decision tuple must be updated to the new set, with the comment `# Stage 1 decision set, spec 2026-09-04`.

- [ ] **Step 8: Mutation check**

Make `consent` always `("advance",)`: the first test fails. Restore.

- [ ] **Step 9: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_intents.py tests/unit/test_timeboxing_intents.py
git commit -m "feat(slack): the Stage 1 decision set; Next only after the kernel proposed; answers bind to the open cell

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Cards: stage from the requirement, the gate line, Next on GateMet

**Files:**
- Modify: `src/fateforger/slack_bot/stage_cards.py` (`Control` union :123-134, `StageCard` :136-149, delete `_QUESTION_STAGE` :249-253, `map_outcome` :308-345)
- Modify: `src/fateforger/slack_bot/timeboxing_cards.py` (control rendering :366-435, imports :34-40)
- Test: `tests/unit/test_stage_cards.py`, `tests/unit/test_render_stage_card.py`

**Interfaces:**
- Consumes: Task 3's `GateMet`, `Gate`, `AwaitingUser.gate`; Task 4's `row_label`, `criterion_label`; Task 5's `TimeboxRequirements.stage_of`.
- Produces: `NextControl(kind="next")` in `Control`; `StageCard.gate: str | None = None`; `DecidedItem.filed_by: Literal["planner", "user"] | None = None`; `map_outcome(GateMet)` returns a stage-1 card with `gate` and `[NextControl, BackControl, CancelControl]`; the renderer draws Next as a primary button carrying `decision="advance"`.

**Coordination:** the #266 session extends `StageCard` on top of exactly these two additions (`gate`, `NextControl`), named to it on 2026-09-04. Do not add anchor groups or deny controls here; those are its half.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_stage_cards.py`:

```python
from fateforger.agents.timeboxing.session_contracts import CellRef, Gate, GateMet
from fateforger.slack_bot.stage_cards import NextControl


def _map(outcome, snapshot):
    return map_outcome(
        outcome, snapshot, pending=PendingTimeboxCandidates(), actor_user_id="U1",
        session_key="C1:1.0", channel_id="C1", thread_ts="1.0",
    )


def test_gate_met_is_a_stage_one_card_with_next_and_the_closing_line() -> None:
    card = _map(GateMet(gate=Gate(open_cells=[], day_label="working Tuesday")), _snapshot())
    assert card.stage.index == 1
    assert card.gate == "That's what I know to ask about a working Tuesday. Anything else, or shall I plan?"
    assert [type(c) for c in card.controls][0] is NextControl
    assert card.asking is None


def test_a_probe_card_names_what_is_still_needed_and_offers_no_next() -> None:
    gate = Gate(open_cells=[CellRef(row="body", criterion="unclear")], day_label="working Tuesday")
    pending = PendingBlocker(requirement_id="elicit.body.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    outcome = AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="body", gate=gate)
    card = _map(outcome, _snapshot(pending_blocker=pending))
    assert card.stage.index == 1
    assert card.gate == "Still need: body, clarity."
    assert not any(isinstance(c, NextControl) for c in card.controls)


def test_the_stage_of_a_question_comes_from_the_catalog() -> None:
    pending = PendingBlocker(requirement_id="skeleton.requested_activity", fact_kind=FactKind.REQUESTED_ACTIVITY, options=[])
    outcome = AwaitingUser(requirement_id="skeleton.requested_activity", question="q", why_needed="w")
    assert _map(outcome, _snapshot(pending_blocker=pending)).stage.index == 2
    frame = PendingBlocker(requirement_id="skeleton.day_frame", fact_kind=FactKind.DAY_FRAME, options=[])
    outcome = AwaitingUser(requirement_id="skeleton.day_frame", question="q", why_needed="w")
    assert _map(outcome, _snapshot(pending_blocker=frame)).stage.index == 1
```

Append to `tests/unit/test_render_stage_card.py` (mirror its existing helper for building a card and reading the actions block):

```python
def test_next_renders_as_a_primary_button_that_advances() -> None:
    from fateforger.slack_bot.stage_cards import NextControl, StageCard, stage
    from fateforger.slack_bot.timeboxing_cards import render_stage_card
    from fateforger.slack_bot.timeboxing_intents import intent_from_artifact_action
    from fateforger.agents.timeboxing.session_contracts import Advance

    card = StageCard(stage=stage(1), session_key="C1:1.0", expected_revision=4, gate="ok", controls=[NextControl()])
    message = render_stage_card(card)
    [button] = [
        el for block in message.blocks if block.get("type") == "actions" for el in block["elements"]
        if el["text"]["text"] == "Next"
    ]
    assert button["style"] == "primary"
    assert intent_from_artifact_action(button["value"]).intent == Advance()
    assert any("ok" in json.dumps(block) for block in message.blocks)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_cards.py tests/unit/test_render_stage_card.py -v -k "gate or next or catalog"`
Expected: FAIL with `ImportError: NextControl`.

- [ ] **Step 3: Models**

In `stage_cards.py`, after `class CancelControl`:

```python
class NextControl(_Frozen):
    """Consent to close Stage 1. Drawn only from a `GateMet` outcome."""

    kind: Literal["next"] = "next"
```

Add `NextControl,` to the `Control` union. In `StageCard`, after `asking`:

```python
    #: The gate line, always present on a Stage 1 card: what is still needed,
    #: or the proposal to close. Rendered from typed fields; nothing reads it.
    gate: str | None = None
```

Import `GateMet`, `Gate` from `session_contracts` and `criterion_label`, `row_label` from `fateforger.agents.timeboxing.elicitation`; import `TimeboxRequirements` from `fateforger.agents.timeboxing.readiness`.

- [ ] **Step 4: Delete the map, read the catalog, render the gate**

Delete `_QUESTION_STAGE`. Add:

```python
def _gate_line(gate: Gate) -> str:
    if not gate.open_cells:
        return (
            f"That's what I know to ask about a {gate.day_label}. "
            "Anything else, or shall I plan?"
        )
    needs = ", ".join(
        f"{row_label(cell.row)}, {criterion_label(cell.criterion)}" for cell in gate.open_cells
    )
    return f"Still need: {needs}."
```

In `map_outcome`, replace the `AwaitingUser` branch's stage lookup and add the gate:

```python
    if isinstance(outcome, AwaitingUser):
        index = TimeboxRequirements.stage_of(outcome.requirement_id)
        return StageCard(
            stage=stage(index),
            session_key=session_key,
            expected_revision=snapshot.revision,
            decided=_decided(snapshot),
            asking=Asking(
                requirement_id=outcome.requirement_id,
                question=outcome.question,
                why_needed=outcome.why_needed,
                options=list(outcome.options),
            ),
            gate=None if outcome.gate is None else _gate_line(outcome.gate),
            controls=_nav(back=True),
        )
    if isinstance(outcome, GateMet):
        return StageCard(
            stage=stage(1),
            session_key=session_key,
            expected_revision=snapshot.revision,
            decided=_decided(snapshot),
            gate=_gate_line(outcome.gate),
            controls=[NextControl(), *_nav(back=True)],
        )
```

In `DecidedItem`, add a typed field so a renderer never reads who filed an assumption out of a label (the #266 session's deny control renders differently for a user-filed assumption):

```python
class DecidedItem(_Frozen):
    text: str
    kind: Literal["assumption", "fact"]
    ref: str
    #: Present for an assumption: who supplied it. A renderer that needs this
    #: reads the field, never the text.
    filed_by: Literal["planner", "user"] | None = None
```

In `_decided`, pass it: `DecidedItem(text=f"{_as_text(assumption.value)} — {assumption.why_needed}", kind="assumption", ref=assumption.assumption_id, filed_by=assumption.filed_by)`. The text is unchanged. Add to the stage-card tests: an assumption with `filed_by="user"` maps to a `DecidedItem` whose `filed_by == "user"`.

- [ ] **Step 5: Render**

In `timeboxing_cards.py`, import `NextControl` beside the other controls. After the `card.asking` block and before `nav`, render the gate line:

```python
    if card.gate:
        blocks.append(_section(card.gate))
        text_lines.append(card.gate)
```

In the control loop, before the `BackControl` branch:

```python
        elif isinstance(control, NextControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID,
                    "Next",
                    artifact_action_value(
                        session_key=card.session_key,
                        expected_revision=card.expected_revision,
                        decision="advance",
                        artifact=None,
                    ),
                    primary=True,
                )
            )
```

`FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID` is the action id whose handler already turns `decision="advance"` into `Advance()` (the failure card's "Try that again" uses it). Reusing it means no new Slack handler registration.

- [ ] **Step 6: Run the card suites**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_cards.py tests/unit/test_render_stage_card.py tests/unit/test_stage_card_registry.py -v`
Expected: all PASS. Any test that imported `_QUESTION_STAGE` is rewritten to assert through `map_outcome`.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/slack_bot/stage_cards.py src/fateforger/slack_bot/timeboxing_cards.py tests/unit/test_stage_cards.py tests/unit/test_render_stage_card.py
git commit -m "feat(slack): a question's stage comes from the catalog; GateMet renders the gate line and Next (#276)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: The feedback observer port, wired after the save

**Files:**
- Create: `src/fateforger/agents/timeboxing/feedback.py`
- Modify: `src/fateforger/slack_bot/handlers.py:1626-1650` (after `kernel.turn`)
- Test: `tests/unit/test_feedback_observer.py` (new)

**Interfaces:**
- Consumes: `PlanningSessionSnapshot`, `FactKind.ELICITED_STATEMENT`/`SUSPENDED_CONSTRAINT`.
- Produces: `FeedbackObserver` protocol with `async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None`; `feedback_facts(before, after) -> list[PlanningFact]` (user-sourced Stage 1 facts new since `before`); `RecordingFeedbackObserver` (keeps what it was given); `NullFeedbackObserver`.

**Scope note:** the transport that reaches `memory_observe` on a distinct channel is not built here. The Slack process holds a read-only KG client and no judge; the harness reaches memory over MCP. Which of those carries feedback is a follow-up ticket to file when this task lands. What this task guarantees is the invariant the spec states: the call happens after the save succeeded, with exactly the user-sourced Stage 1 facts, and never before.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_feedback_observer.py
"""Stage 1 answers and suspensions are offered to memory after the save, as
reader feedback with provenance: recorded, never acted on."""
from __future__ import annotations

from fateforger.agents.timeboxing.feedback import RecordingFeedbackObserver, feedback_facts
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningFact,
    PlanningSessionSnapshot,
)


def _snapshot(*facts) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(session_key="C1:1.0", revision=1, owner_user_id="U1", facts=list(facts))


def test_only_new_user_sourced_stage_one_facts_are_feedback() -> None:
    old = PlanningFact(fact_id="elicited:x:1", kind=FactKind.ELICITED_STATEMENT, value={"cell": None, "text": "old"}, source="user")
    new = PlanningFact(fact_id="elicited:x:2", kind=FactKind.ELICITED_STATEMENT, value={"cell": None, "text": "new"}, source="user")
    suspend = PlanningFact(fact_id="suspend:c1", kind=FactKind.SUSPENDED_CONSTRAINT, value={"uid": "c1", "reason": "not today"}, source="user")
    request = PlanningFact(fact_id="activity-1", kind=FactKind.REQUESTED_ACTIVITY, value="gym", source="user")
    system = PlanningFact(fact_id="coverage:2026-09-08", kind=FactKind.COVERAGE_MATRIX, value={"cells": {}}, source="system")

    picked = feedback_facts(_snapshot(old), _snapshot(old, new, suspend, request, system))

    assert [f.fact_id for f in picked] == ["elicited:x:2", "suspend:c1"]


async def test_the_recording_observer_keeps_what_it_was_given() -> None:
    observer = RecordingFeedbackObserver()
    fact = PlanningFact(fact_id="suspend:c1", kind=FactKind.SUSPENDED_CONSTRAINT, value={"uid": "c1", "reason": "not today"}, source="user")
    await observer.observe(session_key="C1:1.0", facts=[fact])
    assert observer.observed == [("C1:1.0", [fact])]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_feedback_observer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: The module**

```python
# src/fateforger/agents/timeboxing/feedback.py
"""Reader feedback from Stage 1 to the memory server: recorded, never acted on.

Every answer the user gives a probe and every rule they set aside is a signal
about whether what memory surfaced was right. #140 rules that such outcome
data may only ever be a rollback monitor, and memobase's unread `update_hits`
counter is the shape to avoid: so this port records with provenance and
nothing that plans reads it back.

The call happens after the snapshot save succeeded, never before: state
advances on durable success, not on intent.

The transport (in-process service with a judge, or the memory MCP server
over its `memory_observe` tool on a distinct channel) is not decided here; the
Slack process holds a read-only client today. `RecordingFeedbackObserver` is
what tests and the demo wire until it is.
"""
from __future__ import annotations

from typing import Protocol

from .session_contracts import FactKind, PlanningFact, PlanningSessionSnapshot

_FEEDBACK_KINDS = frozenset({FactKind.ELICITED_STATEMENT, FactKind.SUSPENDED_CONSTRAINT})


class FeedbackObserver(Protocol):
    async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None: ...


def feedback_facts(
    before: PlanningSessionSnapshot, after: PlanningSessionSnapshot
) -> list[PlanningFact]:
    """User-sourced Stage 1 facts present in `after` and not in `before`, by id."""
    seen = {fact.fact_id for fact in before.facts}
    return [
        fact
        for fact in after.facts
        if fact.fact_id not in seen and fact.kind in _FEEDBACK_KINDS and fact.source == "user"
    ]


class RecordingFeedbackObserver:
    def __init__(self) -> None:
        self.observed: list[tuple[str, list[PlanningFact]]] = []

    async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None:
        self.observed.append((session_key, list(facts)))


class NullFeedbackObserver:
    async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None:
        return None
```

- [ ] **Step 4: Wire the call after the save**

In `handlers.py`, `_run_adaptive_timebox_turn`, the kernel turn is followed by `current = await repository.load_or_create(...)`. Directly after that line:

```python
        observer = getattr(runtime, "timeboxing_feedback_observer", None)
        if observer is not None:
            new_feedback = feedback_facts(snapshot, current)
            if new_feedback:
                try:
                    await observer.observe(session_key=session_key, facts=new_feedback)
                except Exception as exc:  # noqa: BLE001 - feedback must not fail the turn
                    logger.warning(
                        "stage1 feedback not recorded error_type=%s count=%d",
                        type(exc).__name__,
                        len(new_feedback),
                    )
```

Import `feedback_facts` from `fateforger.agents.timeboxing.feedback`. The log line carries a count and an error type, never a fact's text. `snapshot` here is the pre-turn snapshot already loaded above in the same function; if a rename is needed to keep it, keep the pre-turn value under `before`.

- [ ] **Step 5: Test the wiring**

Append to `tests/unit/test_feedback_observer.py` a test that drives `_run_adaptive_timebox_turn` with a `RecordingFeedbackObserver` on a fake runtime and a `ProvidePlanningFacts` intent carrying one `SUSPENDED_CONSTRAINT` fact, then asserts `observer.observed == [(session_key, [that fact])]`. Build the fake runtime the way `tests/unit/test_harness_approval_action.py` builds one for the same function (copy its helper and remove what this test does not need). Then a second test with an observer whose `observe` raises, asserting the turn still returns its outcome and the log contains `stage1 feedback not recorded` (use `caplog`).

- [ ] **Step 6: Run and commit**

Run: `.venv/bin/python -m pytest tests/unit/test_feedback_observer.py -v`
Expected: all PASS.

```bash
git add src/fateforger/agents/timeboxing/feedback.py src/fateforger/slack_bot/handlers.py tests/unit/test_feedback_observer.py
git commit -m "feat(timeboxing): feedback observer port, called after the save with the user's Stage 1 facts

Transport to memory_observe is a follow-up; the invariant lands here.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: The shared fixture (#283)

**Files:**
- Create: `tests/fixtures/stage1/__init__.py`, `tests/fixtures/stage1/days.py`, `tests/fixtures/stage1/labels.toml`
- Create: `tests/evals/test_stage1_fixture.py`
- Test: the eval file itself, plus `tests/unit/test_stage1_fixture_shapes.py` (new)

**Interfaces:**
- Consumes: Task 3's snapshot fields; Task 4's `CONCERNS`, `CRITERIA`, `ALL_CELLS`; Task 2's row shape; `ConstraintStore`, `AnchorStore`, `get_active_constraints`.
- Produces: `FIXTURE_DAYS: tuple[FixtureDay, ...]` with `key`, `date`, `day_type`, `request`; `snapshot_for(day: FixtureDay, rows: list[dict]) -> PlanningSessionSnapshot`; `rows_for(db_path: str, day: FixtureDay) -> list[dict]`; `load_labels(path) -> dict[str, list[LabelledGap]]` with `LabelledGap(cell: str, hard: bool, note: str)`.

- [ ] **Step 1: Write the failing shape test**

```python
# tests/unit/test_stage1_fixture_shapes.py
"""The fixture is three locked days, one request, and a label file whose cells
name real cells. Everything a spike reads is asserted here offline."""
from __future__ import annotations

from pathlib import Path

from fateforger.agents.timeboxing.elicitation import ALL_CELLS
from tests.fixtures.stage1.days import FIXTURE_DAYS, load_labels, snapshot_for

LABELS = Path(__file__).resolve().parents[1] / "fixtures" / "stage1" / "labels.toml"


def test_three_days_with_the_same_request() -> None:
    assert [d.key for d in FIXTURE_DAYS] == ["working_tuesday", "vacation_day", "sunday"]
    assert {d.request for d in FIXTURE_DAYS} == {"deep work in the morning, gym at 18:00"}
    assert [d.date.strftime("%A") for d in FIXTURE_DAYS] == ["Tuesday", "Wednesday", "Sunday"]


def test_a_fixture_snapshot_is_locked_and_carries_its_rows() -> None:
    rows = [{"uid": "c1", "name": "x", "necessity": "must", "anchors": []}]
    snapshot = snapshot_for(FIXTURE_DAYS[0], rows)
    assert snapshot.planning_day is not None
    assert snapshot.applicable_constraints == rows
    assert snapshot.stage1 == "open"


def test_labels_name_real_cells_and_every_day() -> None:
    labels = load_labels(LABELS)
    assert set(labels) == {d.key for d in FIXTURE_DAYS}
    valid = {cell.id for cell in ALL_CELLS}
    for day, gaps in labels.items():
        for gap in gaps:
            assert gap.cell in valid, (day, gap.cell)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_stage1_fixture_shapes.py -v`
Expected: FAIL with `ModuleNotFoundError: tests.fixtures.stage1`.

- [ ] **Step 3: The fixture module**

`tests/fixtures/stage1/__init__.py` is empty. `tests/fixtures/stage1/days.py`:

```python
"""The shared Stage 1 fixture (#283): three locked days, one request.

Every spike runs exactly this. The store is never the live one: `rows_for`
takes a path the caller copied into a temp dir. Hand labels live in
labels.toml beside this file; a day with no labels fails loudly, because a
spike measured against no ground truth measures the model's opinion of itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import tomllib

from fateforger.agents.timeboxing.session_contracts import (
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)


@dataclass(frozen=True, slots=True)
class FixtureDay:
    key: str
    date: date
    day_type: DayType
    request: str


REQUEST = "deep work in the morning, gym at 18:00"

FIXTURE_DAYS: tuple[FixtureDay, ...] = (
    FixtureDay("working_tuesday", date(2026, 9, 8), DayType.WORKING, REQUEST),
    FixtureDay("vacation_day", date(2026, 9, 9), DayType.VACATION, REQUEST),
    FixtureDay("sunday", date(2026, 9, 13), DayType.WEEKEND, REQUEST),
)


def snapshot_for(day: FixtureDay, rows: list[dict]) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key=f"fixture:{day.key}",
        revision=1,
        owner_user_id="U_FIXTURE",
        planning_day=PlanningDay.lock_default(
            value=day.date, timezone="Europe/Amsterdam", lock_revision=1, day_type=day.day_type
        ),
        facts=[
            PlanningFact(
                fact_id="request-1", kind=FactKind.REQUESTED_ACTIVITY, value=day.request, source="user"
            )
        ],
        applicable_constraints=rows,
    )


def rows_for(db_path: str, day: FixtureDay) -> list[dict]:
    """The rows the KG client would hand the host for this day, from a copy."""
    from fateforger.agents.timeboxing.kg_constraint_client import KGConstraintMemoryClient
    import asyncio

    client = KGConstraintMemoryClient(db_path)
    return asyncio.run(
        client.query_constraints(
            filters={"planned_day": day.date.isoformat(), "day_type": day.day_type.value}, limit=200
        )
    )


@dataclass(frozen=True, slots=True)
class LabelledGap:
    cell: str
    hard: bool
    note: str


def load_labels(path: Path) -> dict[str, list[LabelledGap]]:
    raw = tomllib.loads(path.read_text())
    labels: dict[str, list[LabelledGap]] = {}
    for day_key, section in raw.items():
        labels[day_key] = [
            LabelledGap(cell=str(g["cell"]), hard=bool(g.get("hard", False)), note=str(g.get("note", "")))
            for g in section.get("gaps", [])
        ]
    return labels
```

`tests/fixtures/stage1/labels.toml`, the schema with Hugo's entries still to come. TOML through the standard library's `tomllib`; PyYAML is not a dependency of this project.

```toml
# Hand-labelled gaps a good coach would ask about, per fixture day (#283).
# cell: one of elicit.<row>.<criterion>; hard = true means a spike that misses
# it fails the recall threshold. Hugo fills these before any spike runs.

[working_tuesday]
gaps = [
  { cell = "elicit.request.tacit_knowledge", hard = true, note = "the deep-work block has no duration and no start time" },
]

[vacation_day]
gaps = []

[sunday]
gaps = []
```

The two empty days are deliberate and the eval below refuses to run on them; the one working-Tuesday entry is the gap the spike measured eleven times and is there so the loader has a real row to parse.

- [ ] **Step 4: The eval that builds the store copy**

```python
# tests/evals/test_stage1_fixture.py
"""Builds the shared fixture against a copy of a real store. Marked slow.

Set STAGE1_FIXTURE_DB to a memory.db to copy; without it the tests skip with
that reason. The live data/memory.db is never opened in place.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.fixtures.stage1.days import FIXTURE_DAYS, load_labels, rows_for, snapshot_for

pytestmark = pytest.mark.slow

LABELS = Path(__file__).resolve().parents[1] / "fixtures" / "stage1" / "labels.toml"


@pytest.fixture
def store_copy(tmp_path) -> str:
    source = os.environ.get("STAGE1_FIXTURE_DB", "").strip()
    if not source:
        pytest.skip("STAGE1_FIXTURE_DB not set; the fixture needs a store to copy")
    target = tmp_path / "memory.db"
    shutil.copy(source, target)
    return str(target)


@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=[d.key for d in FIXTURE_DAYS])
def test_each_day_yields_rows_and_a_locked_snapshot(store_copy, day) -> None:
    rows = rows_for(store_copy, day)
    snapshot = snapshot_for(day, rows)
    assert snapshot.applicable_constraints == rows
    assert all("anchors" in row for row in rows)


@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=[d.key for d in FIXTURE_DAYS])
def test_every_day_has_hand_labels_before_a_spike_may_run(day) -> None:
    labels = load_labels(LABELS)
    if not labels.get(day.key):
        pytest.fail(f"{day.key} has no hand-labelled gaps; a spike against it measures nothing")
```

Create `tests/evals/__init__.py` if the directory is new and other test packages have one.

- [ ] **Step 5: Run**

Run: `.venv/bin/python -m pytest tests/unit/test_stage1_fixture_shapes.py -v && STAGE1_FIXTURE_DB=$(pwd)/data/memory.db .venv/bin/python -m pytest tests/evals/test_stage1_fixture.py -v -m slow`
Expected: the shape tests PASS; the eval's row tests PASS on the working Tuesday with 41 rows; the label tests FAIL for `vacation_day` and `sunday` with the message naming the day. That failure is the fixture doing its job and stays red until Hugo labels those days.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/stage1 tests/evals/test_stage1_fixture.py tests/unit/test_stage1_fixture_shapes.py
git commit -m "test(stage1): the shared spike fixture: three locked days, store copy, hand-label schema (#283)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Whole-suite run, demo boot, and the tickets

**Files:**
- Modify: none in `src/` unless a failure demands it.

- [ ] **Step 1: Full unit suite**

Run: `.venv/bin/python -m pytest tests/unit tests/memory -q -m "not slow"`
Expected: all PASS.

- [ ] **Step 2: Bare boot check**

Run: `.venv/bin/python scripts/demo.py status`
Expected: the profile loads and exactly one bot is reported (or none, if the stack is down); no import error from the new module.

- [ ] **Step 3: Tickets**

```bash
gh issue comment 262 --body "Phase 1 groundwork landed: after the day is confirmed the session now receives the active rules, proposes to close Stage 1 with a typed gate, and offers Next only then. Plan: docs/superpowers/plans/2026-09-04-stage1-elicitation-groundwork.md. The gap-filling loop is #284/#286."
gh issue comment 276 --body "Closed by the catalog: every ArtifactRequirement carries stage, the card reads it, and _QUESTION_STAGE is gone. Ladder is 1,1,...,2,3 by construction."
gh issue create --title "stage1: feedback observer transport to memory_observe on a distinct channel" --body "Task 10 of docs/superpowers/plans/2026-09-04-stage1-elicitation-groundwork.md landed the FeedbackObserver port and its call after the save. The transport is undecided: the Slack process holds a read-only KG client and no judge; the harness reaches memory over MCP. Decide which carries user-sourced ELICITED_STATEMENT and SUSPENDED_CONSTRAINT facts to memory_observe with provenance (rule uid, session, action), on a channel nothing planning reads. Spec: Steering semantics, 'The feedback channel'."
```

- [ ] **Step 4: Push**

```bash
git push origin main
```

---

## Self-review

**Spec coverage.** Sequence step 3 lists nine shapes; each has a task: `AnchorRef` and the join (1, 2); the three fact kinds (3); `Gate`, `GateMet`, `stage1_gate` (3, 4, 6, 8, 9); `stage` on requirements and the forty cells (5); `filed_by` and `DenyAssumption` (3, 6, 8); `applicable_constraints` on the snapshot (3, 6, 7); the Stage 1 decision set (8); the shared fixture (11). Suspension enforced at the brief (6). Next only on `GateMet` (8, 9). Consent as the next message and re-entry on a Stage 1 fact (6). `steer_always` is deliberately not offered (8) and its ask-first flow is Increment B's, after the spikes. The feedback transport is a follow-up ticket (12) and the port is real (10).

**Placeholders.** None; every step has its code or its exact command. Names the plan relies on were verified against the tree on 2026-09-04: `PlanningDay.date`, `DayType.VACATION`/`WEEKEND`, `InMemoryPlanningSessionRepository([snapshots])`, and the absence of PyYAML (hence `tomllib`).

**Amendments 2026-09-04, from the #266 grammar session:** Task 1b (`fade`), `count_suspended` and `suspended_constraint_count` (Tasks 2, 3, 6, 7), `RestoreConstraint` and the `restore` decision (Tasks 3, 6, 8), typed `filed_by` on `DecidedItem` (Task 9).

**Type consistency.** `Gate(open_cells, day_label, note)` is the same in Tasks 3, 4, 6, 9. `RestoreConstraint(constraint_uid)` in 3, 6, 8; `suspended_constraint_count` in 3, 6, 7. `CellRef.id` is `elicit.{row}.{criterion}` in 3, 4, 5. `stage1` values are `open | proposed | closed` in 3, 6, 8, 9. `feedback_facts(before, after)` in 10 matches its call. `FileAssumption(requirement_id, value, why_needed)` in 3, 6, 8. The `advance` decision remains the schema name for the spec's `next`; the spec's table is the intent, this plan keeps the existing literal so the button and every handler keep working.
