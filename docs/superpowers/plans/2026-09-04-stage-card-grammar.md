# Stage-card grammar (context panel, fold, decided overflow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the looping Stage 1 a surface that fits Slack: a compact turn card per probe, a two-block context panel posted once per stage and edited in place, and a modal fold that lists every active rule with a steer menu.

**Architecture:** Three typed models, three builders, three renderers, one control table. `StageCard` (existing) stays the per-turn card and gains a deny overflow on decided items. A new pure module `stage_context.py` builds `ContextPanel` and `ContextFold` from the snapshot alone, ordering rows by what changed and what is uncertain first. The registry learns one panel per session and keeps it current; handlers open the fold as a modal and route its overflow picks through the existing `intent_from_artifact_action`.

**Tech Stack:** Python 3.11, Pydantic v2 (strict, frozen models), slack_bolt / slack_sdk (async), pytest + pytest-asyncio, `blockkit` 2.1.3 as a dev dependency for Block Kit validation in tests.

**Spec:** `docs/superpowers/specs/2026-09-04-stage-card-grammar-design.md`.
**Lands after:** Phase 1, `docs/superpowers/plans/2026-09-04-stage1-elicitation-groundwork.md`, merged to main. Every name below marked *(Phase 1)* comes from that plan and must exist on main before Task 1 starts: `PlanningSessionSnapshot.applicable_constraints`, `.suspended_constraint_count`, `.stage1`; `FactKind.SUSPENDED_CONSTRAINT`, `FactKind.COVERAGE_MATRIX`; `suspension_fact_id(uid)`; `RestoreConstraint`, `DenyAssumption`; `ArtifactActionMeta.decision` values `deny_assumption` and `restore`; `NextControl`, `StageCard.gate`, `DecidedItem.filed_by`; `fateforger.agents.timeboxing.elicitation.coverage_matrix`, `.day_label`, `.ALL_CELLS`; rows carrying `"anchors": [{"uid","name"}]`, `"fade": float | None` and, if Task 1c landed, `"applies"`.

## Global Constraints

- **No pattern matching on user content, ever** (CLAUDE.md). Every comparison in this plan is over identifiers this system minted: constraint uids, anchor uids, fact ids, enum values. Anchor *names* are displayed, never compared.
- **The read path never calls a model.** `stage_context.py` calls no model and opens no store; it reads the snapshot and nothing else.
- **Block Kit caps as constants in one place** (`messages.py`): `SLACK_MAX_BLOCKS = 40` per message, `SLACK_MAX_BLOCK_TEXT_CHARS = 1600` per section (project cap under Slack's 3000). New constant `SLACK_MAX_MODAL_BLOCKS = 100`. Overflow menus hold at most 5 options; a section has one accessory.
- **Truncation is by count, never by slicing text.** A list that does not fit drops whole items and says how many.
- **Decomposition discipline:** a result posts as a new message; the pressed card shrinks to a receipt; the panel is edited in place and never grows.
- **Every drawn control decodes through `intent_from_artifact_action`** to an intent the kernel accepts at that revision; every button has a typed equivalent through `SurfaceIntentInterpreter` (Phase 1 Task 8 offers the decisions and the uids).
- **Best-effort presentation:** a failed `chat_update`, `views_open` or `views_update` is logged and never fails the turn.
- **Unit tests opt in to the harness backend** the way `tests/unit/test_timebox_session_surface.py:81` does; `tests/conftest.py` pins legacy globally.
- **Every guard is broken on purpose once** before it is trusted (CLAUDE.md).
- Run everything with `.venv/bin/python -m pytest`; the memory server needs `PYTHONPATH=src` but nothing in this plan imports it.

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `src/fateforger/slack_bot/messages.py` | `SLACK_MAX_MODAL_BLOCKS = 100` | 1 |
| `src/fateforger/slack_bot/stage_context.py` (new) | `AnchorGroup`, `SuspendedRow`, `FoldRow`, `FoldGroup`, `ContextPanel`, `ContextFold`; `rank_rows`, `group_rows`, `context_panel`, `context_fold`. Pure. | 1, 2, 3 |
| `src/fateforger/slack_bot/stage_cards.py` | `DenyControl` in the `Control` union; `DecidedItem.controls`; `_decided` attaches the deny control | 4 |
| `src/fateforger/slack_bot/timeboxing_intents.py` | `ArtifactActionMeta.decision` gains `steer_not_today` with `constraint_uid` and optional `note`; bound to `ProvidePlanningFacts` | 5 |
| `src/fateforger/slack_bot/timeboxing_cards.py` | `render_context_panel`, `render_context_fold`, decided overflow, free-association hint, three action ids | 6 |
| `pyproject.toml` | `blockkit = "^2.1.3"` in the dev group | 6 |
| `src/fateforger/slack_bot/stage_card_registry.py` | `ShownPanel`, `remember_panel`, `panel_shown`, `sync_panel` | 7 |
| `src/fateforger/slack_bot/handlers.py` | `sync_panel` after `transition`; `show_rules` → `views_open`; steer and decided overflow picks → the artifact action path, then `views_update` | 8, 9 |
| `tests/unit/test_stage_context.py` (new) | builders, ordering, grouping, truncation, AST guard | 1, 2, 3 |
| `tests/unit/test_stage_cards.py` | deny control on decided items | 4 |
| `tests/unit/test_timeboxing_intents_steer.py` (new) | `steer_not_today` press round-trip | 5 |
| `tests/unit/test_render_context_surfaces.py` (new) | structural renderer tests + `blockkit` validation of every surface | 6 |
| `tests/unit/test_stage_card_registry.py` | panel record and `sync_panel` | 7 |
| `tests/unit/test_stage_panel_in_the_turn.py` (new) | panel posted once, edited on change, re-posted on day change, through `_run_adaptive_timebox_turn` | 8 |
| `tests/unit/test_fold_modal_handlers.py` (new) | `show_rules` opens the modal; an overflow pick reaches the turn and refreshes the modal | 9 |
| `tests/e2e/test_stage1_panel_walk.py` (new) | ten probe turns → ten cards, one panel; a steer edits the panel | 10 |

The spec also names `PromoteControl` (*Always, asks first*) on user-stated facts. It is **not in this plan**: Phase 1 Task 8 keeps `steer_always` out of the offered decisions until the feedback-observer transport lands, and a control with no honourable decision behind it is exactly the button that lies. It follows in the plan that lands that transport; the spec's Coordination section records this.

---

### Task 1: `stage_context.py`: models, `rank_rows`, `group_rows`

**Files:**
- Create: `src/fateforger/slack_bot/stage_context.py`
- Modify: `src/fateforger/slack_bot/messages.py` (one constant)
- Test: `tests/unit/test_stage_context.py`

**Interfaces:**
- Consumes *(Phase 1)*: `PlanningSessionSnapshot.applicable_constraints: list[dict]` with keys `uid`, `name`, `necessity` (`"must"|"should"`), `anchors: list[{"uid","name"}]`, `fade: float | None`, optionally `applies: "every_day"|"some_days"|"dated"`; `FactKind.SUSPENDED_CONSTRAINT` facts with `fact_id == suspension_fact_id(uid)` and `value == {"uid": ..., "reason": ...}`; `fateforger.agents.timeboxing.elicitation.coverage_matrix(snapshot) -> CoverageMatrix | None` with `.cells: dict[str, str]` keyed `elicit.{row}.{criterion}` and `.placement: dict[str, str]` keyed by anchor uid → row key; `ALL_CELLS: tuple[CellRef, ...]` with `.row`, `.criterion`.
- Produces: `RankedRow(uid, name, necessity, anchors: list[tuple[str, str]], fade, applies, suspended_reason, touched, open_concern)`; `rank_rows(snapshot, first_shown_with: frozenset[str] | None) -> list[RankedRow]`; `AnchorGroup(name: str | None, uids: list[str], must_count: int)`; `group_rows(rows: list[RankedRow]) -> list[AnchorGroup]`; `primary_anchor(row, sizes) -> str | None`; `SLACK_MAX_MODAL_BLOCKS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_stage_context.py
"""Panel and fold builders: every assertion is over uids, anchor uids,
fact ids, counts and enum values this system minted. Nothing reads a name."""

from __future__ import annotations

from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    suspension_fact_id,
)
from fateforger.slack_bot.stage_context import group_rows, rank_rows


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1
    )


def _row(uid: str, *anchors: tuple[str, str], necessity="should", fade=None) -> dict:
    return {
        "uid": uid,
        "name": f"rule {uid}",
        "necessity": necessity,
        "anchors": [{"uid": a, "name": n} for a, n in anchors],
        "fade": fade,
    }


def _suspend(uid: str) -> PlanningFact:
    return PlanningFact(
        fact_id=suspension_fact_id(uid),
        kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": uid, "reason": "not today"},
        source="user",
    )


def _snapshot(rows: list[dict], facts: list[PlanningFact] | None = None, **update):
    base = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        applicable_constraints=rows,
        facts=list(facts or []),
    )
    return base.model_copy(update=update)


GYM, BREAKFAST, DINNER = ("a-gym", "gym"), ("a-bf", "breakfast"), ("a-din", "dinner")


def test_store_order_is_the_tiebreak() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM), _row("c3", GYM)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c1", "c2", "c3"]


def test_a_suspended_rule_ranks_first() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    ranked = rank_rows(_snapshot(rows, [_suspend("c2")]), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c2", "c1"]
    assert ranked[0].suspended_reason == "not today"
    assert ranked[0].touched is True


def test_a_rule_absent_from_the_first_draw_ranks_first() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=frozenset({"c1"}))
    assert [r.uid for r in ranked] == ["c2", "c1"]


def test_nearest_to_fading_ranks_before_the_store_order_and_none_last() -> None:
    rows = [_row("c1", GYM, fade=None), _row("c2", GYM, fade=0.2), _row("c3", GYM, fade=0.9)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c3", "c2", "c1"]


def test_touched_outranks_fading() -> None:
    rows = [_row("c1", GYM, fade=0.9), _row("c2", GYM, fade=None)]
    ranked = rank_rows(_snapshot(rows, [_suspend("c2")]), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c2", "c1"]


def test_a_rule_under_an_open_concern_ranks_before_fading(monkeypatch) -> None:
    import fateforger.slack_bot.stage_context as module

    class Matrix:
        cells = {"elicit.body.unclear": "uncovered", "elicit.fixed.unclear": "covered"}
        placement = {"a-gym": "body", "a-din": "fixed"}

    monkeypatch.setattr(module, "coverage_matrix", lambda snapshot: Matrix())
    rows = [_row("c1", DINNER, fade=0.9), _row("c2", GYM, fade=None)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c2", "c1"]
    assert ranked[0].open_concern is True


def test_every_rule_lands_in_exactly_one_group_the_largest() -> None:
    rows = [
        _row("c1", GYM, BREAKFAST),   # gym has 2 rules, breakfast 2 -> tie by name: breakfast
        _row("c2", GYM),
        _row("c3", BREAKFAST, DINNER),
        _row("c4"),
    ]
    groups = group_rows(rank_rows(_snapshot(rows), first_shown_with=None))
    placed = [uid for g in groups for uid in g.uids]
    assert sorted(placed) == ["c1", "c2", "c3", "c4"]
    by_name = {g.name: g.uids for g in groups}
    assert by_name["breakfast"] == ["c1", "c3"]
    assert by_name["gym"] == ["c2"]
    assert by_name[None] == ["c4"]
    assert "dinner" not in by_name


def test_groups_take_the_rank_of_their_top_row() -> None:
    rows = [_row("c1", GYM), _row("c2", DINNER), _row("c3", DINNER)]
    groups = group_rows(rank_rows(_snapshot(rows, [_suspend("c2")]), first_shown_with=None))
    assert [g.name for g in groups] == ["dinner", "gym"]


def test_must_count_per_group() -> None:
    rows = [_row("c1", GYM, necessity="must"), _row("c2", GYM)]
    (group,) = group_rows(rank_rows(_snapshot(rows), first_shown_with=None))
    assert group.must_count == 1


def test_stage_context_knows_no_slack_and_no_store() -> None:
    import ast
    import inspect

    import fateforger.slack_bot.stage_context as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"slack_sdk", "handlers", "timeboxing_cards", "timeboxing_commit", "memory", "sqlite3"}
    offending = {n for n in imported if any(part in forbidden for part in n.split("."))}
    assert offending == set(), offending
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fateforger.slack_bot.stage_context'`.

- [ ] **Step 3: Add the modal cap constant**

In `src/fateforger/slack_bot/messages.py`, after `SLACK_MAX_BLOCKS = 40`:

```python
#: A modal view holds up to 100 blocks (Slack's cap; a message holds 50 and
#: this project stops at 40). The fold is the one surface that uses it.
SLACK_MAX_MODAL_BLOCKS = 100
```

Add `"SLACK_MAX_MODAL_BLOCKS"` to `__all__`.

- [ ] **Step 4: Write the module: models, ranking, grouping**

```python
# src/fateforger/slack_bot/stage_context.py
"""The context a Stage 1 session is planning against, as two typed surfaces.

A `ContextPanel` is two blocks: counts by anchor group and one control. A
`ContextFold` is the modal behind that control: every active rule, once,
with a steer menu. Both are built from the snapshot and nothing else -- no
model, no store -- and both order rows the same way, through `rank_rows`,
so the panel's summary and the fold never disagree.

Every comparison here is over identifiers this system minted: constraint
uids, anchor uids, fact ids, enum values. Anchor names are displayed and
never compared (CLAUDE.md).
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fateforger.agents.timeboxing.elicitation import ALL_CELLS, coverage_matrix, day_label
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningSessionSnapshot,
    suspension_fact_id,
)

Necessity = Literal["must", "should"]
Applies = Literal["every_day", "some_days", "dated"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RankedRow(_Frozen):
    uid: str
    name: str
    necessity: Necessity
    #: (anchor uid, anchor name), as the memory server returned them.
    anchors: list[tuple[str, str]] = Field(default_factory=list)
    fade: float | None = None
    applies: Applies | None = None
    #: The session suspension's reason, when a SUSPENDED_CONSTRAINT fact names this rule.
    suspended_reason: str | None = None
    #: Ordering key 1: suspended this session, or absent from the first draw.
    touched: bool = False
    #: Ordering key 2: an anchor of this rule is placed under a row with an uncovered cell.
    open_concern: bool = False


class AnchorGroup(_Frozen):
    #: None is the unanchored group.
    name: str | None
    uids: list[str]
    must_count: int


def _suspensions(snapshot: PlanningSessionSnapshot) -> dict[str, str]:
    """Constraint uid -> reason, from the session's SUSPENDED_CONSTRAINT facts."""

    found: dict[str, str] = {}
    for fact in snapshot.facts:
        if fact.kind is not FactKind.SUSPENDED_CONSTRAINT:
            continue
        value = fact.value if isinstance(fact.value, dict) else {}
        uid = value.get("uid")
        if isinstance(uid, str) and fact.fact_id == suspension_fact_id(uid):
            found[uid] = str(value.get("reason") or "not today")
    return found


def _open_rows(snapshot: PlanningSessionSnapshot) -> set[str]:
    """Row keys with at least one uncovered cell. Cell ids are catalog ids."""

    matrix = coverage_matrix(snapshot)
    if matrix is None:
        return set()
    return {
        cell.row
        for cell in ALL_CELLS
        if matrix.cells.get(f"elicit.{cell.row}.{cell.criterion}") == "uncovered"
    }


def _placement(snapshot: PlanningSessionSnapshot) -> dict[str, str]:
    matrix = coverage_matrix(snapshot)
    return dict(matrix.placement) if matrix is not None else {}


def rank_rows(
    snapshot: PlanningSessionSnapshot, first_shown_with: frozenset[str] | None
) -> list[RankedRow]:
    """The snapshot's rows, in the order the panel and the fold show them.

    Four keys, all arithmetic: touched this session; under an open concern;
    nearest to fading (`None` last); then the store's own order, which the
    list already carries, so the sort is stable over it.
    """

    suspended = _suspensions(snapshot)
    open_rows = _open_rows(snapshot)
    placement = _placement(snapshot)
    ranked: list[RankedRow] = []
    for raw in snapshot.applicable_constraints:
        uid = str(raw["uid"])
        anchors = [
            (str(a["uid"]), str(a["name"]))
            for a in (raw.get("anchors") or [])
            if isinstance(a, dict)
        ]
        fade = raw.get("fade")
        ranked.append(
            RankedRow(
                uid=uid,
                name=str(raw["name"]),
                necessity=raw["necessity"],
                anchors=anchors,
                fade=float(fade) if isinstance(fade, (int, float)) else None,
                applies=raw.get("applies"),
                suspended_reason=suspended.get(uid),
                touched=uid in suspended
                or (first_shown_with is not None and uid not in first_shown_with),
                open_concern=any(placement.get(a_uid) in open_rows for a_uid, _ in anchors),
            )
        )
    return sorted(
        ranked,
        key=lambda r: (
            not r.touched,
            not r.open_concern,
            -(r.fade if r.fade is not None else -1.0),
        ),
    )


def primary_anchor(row: RankedRow, sizes: Counter[str]) -> tuple[str, str] | None:
    """The anchor this rule is listed under: the one with the most rules on
    this day, ties by name. A count over minted links, not a judgement."""

    if not row.anchors:
        return None
    return max(row.anchors, key=lambda a: (sizes[a[0]], a[1]))


def group_rows(rows: list[RankedRow]) -> list[AnchorGroup]:
    """Every rule in exactly one group; groups in the order of their top row."""

    sizes: Counter[str] = Counter(a_uid for row in rows for a_uid, _ in row.anchors)
    order: list[str | None] = []
    members: dict[str | None, list[RankedRow]] = {}
    for row in rows:
        primary = primary_anchor(row, sizes)
        name = primary[1] if primary else None
        if name not in members:
            members[name] = []
            order.append(name)
        members[name].append(row)
    return [
        AnchorGroup(
            name=name,
            uids=[r.uid for r in members[name]],
            must_count=sum(1 for r in members[name] if r.necessity == "must"),
        )
        for name in order
    ]


__all__ = ["AnchorGroup", "RankedRow", "group_rows", "primary_anchor", "rank_rows"]
```

Note on `group_rows` keys: two anchors with the same *name* and different uids would
merge here. The memory server mints one anchor per name (`anchors.name` is what
`resolve_anchors` looks up), so a name is a key. If that ever changes, key `members`
by anchor uid and carry the name beside it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_context.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 6: Break one guard on purpose**

Flip `not r.touched` to `r.touched` in the sort key, run
`test_a_suspended_rule_ranks_first`, watch it fail, flip it back.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/slack_bot/stage_context.py src/fateforger/slack_bot/messages.py tests/unit/test_stage_context.py
git commit -m "feat(slack): rank and group the day's rules for the context surfaces (#266)

Touched this session, under an open concern, nearest to fading, then the
store's order; every rule once, under its largest anchor group. Pure over
the snapshot: no model, no store."
```

---

### Task 2: `context_panel`

**Files:**
- Modify: `src/fateforger/slack_bot/stage_context.py`
- Test: `tests/unit/test_stage_context.py`

**Interfaces:**
- Consumes: Task 1's `rank_rows`, `group_rows`; *(Phase 1)* `snapshot.suspended_constraint_count`, `snapshot.planning_day.day_type.value`, `day_label(planning_day)`.
- Produces: `SuspendedRow(uid, name, reason)`; `ContextPanel(session_key, expected_revision, day: str, day_label, rule_count, must_count, off_today_count, off_today_reason, groups: list[AnchorGroup], suspended: list[SuspendedRow], shown_with: frozenset[str], first_shown_with: frozenset[str])`; `context_panel(snapshot, first_shown_with: frozenset[str] | None) -> ContextPanel`; `shown_with_of(snapshot) -> frozenset[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_stage_context.py`:

```python
from fateforger.slack_bot.stage_context import context_panel, shown_with_of


def test_the_panel_counts_and_groups() -> None:
    rows = [_row("c1", GYM, necessity="must"), _row("c2", GYM), _row("c3")]
    panel = context_panel(
        _snapshot(rows, [_suspend("c3")], suspended_constraint_count=3),
        first_shown_with=None,
    )
    assert panel.day == "2026-09-08"
    assert (panel.rule_count, panel.must_count, panel.off_today_count) == (3, 1, 3)
    assert panel.off_today_reason == "working"
    assert [g.name for g in panel.groups] == [None, "gym"]  # suspended c3 ranks first
    assert [s.uid for s in panel.suspended] == ["c3"]
    assert panel.suspended[0].reason == "not today"


def test_shown_with_is_row_uids_plus_suspension_fact_ids() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    snapshot = _snapshot(rows, [_suspend("c2")])
    assert shown_with_of(snapshot) == frozenset({"c1", "c2", suspension_fact_id("c2")})
    panel = context_panel(snapshot, first_shown_with=None)
    assert panel.shown_with == shown_with_of(snapshot)


def test_first_draw_seeds_first_shown_with_and_later_draws_keep_it() -> None:
    first = context_panel(_snapshot([_row("c1", GYM)]), first_shown_with=None)
    assert first.first_shown_with == frozenset({"c1"})
    later = context_panel(
        _snapshot([_row("c1", GYM), _row("c2", GYM)]), first_shown_with=first.first_shown_with
    )
    assert later.first_shown_with == frozenset({"c1"})
    assert [g.uids for g in later.groups] == [["c2", "c1"]]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_context.py -v -k panel`
Expected: FAIL with `ImportError: cannot import name 'context_panel'`.

- [ ] **Step 3: Add the panel model and builder**

Append to `stage_context.py`, before `__all__`:

```python
class SuspendedRow(_Frozen):
    uid: str
    name: str
    reason: str


class ContextPanel(_Frozen):
    session_key: str
    expected_revision: int
    #: ISO date of the locked day; a different day means a different panel.
    day: str
    day_label: str
    rule_count: int
    must_count: int
    #: Rules memory holds that do not apply on this kind of day.
    off_today_count: int
    #: The day type, an enum value.
    off_today_reason: str
    groups: list[AnchorGroup]
    suspended: list[SuspendedRow]
    #: Row uids and suspension fact ids the panel was drawn from. A snapshot
    #: whose set differs needs the panel edited; equal means nothing to do.
    shown_with: frozenset[str]
    #: Row uids at stage entry. Ordering key 1 reads it; kept by the registry.
    first_shown_with: frozenset[str]


def shown_with_of(snapshot: PlanningSessionSnapshot) -> frozenset[str]:
    uids = {str(raw["uid"]) for raw in snapshot.applicable_constraints}
    facts = {
        fact.fact_id
        for fact in snapshot.facts
        if fact.kind is FactKind.SUSPENDED_CONSTRAINT
    }
    return frozenset(uids | facts)


def _row_uids(snapshot: PlanningSessionSnapshot) -> frozenset[str]:
    return frozenset(str(raw["uid"]) for raw in snapshot.applicable_constraints)


def context_panel(
    snapshot: PlanningSessionSnapshot, first_shown_with: frozenset[str] | None
) -> ContextPanel:
    if snapshot.planning_day is None:
        raise ValueError("a context panel needs a locked planning day")
    seed = first_shown_with if first_shown_with is not None else _row_uids(snapshot)
    rows = rank_rows(snapshot, seed if first_shown_with is not None else None)
    return ContextPanel(
        session_key=snapshot.session_key,
        expected_revision=snapshot.revision,
        day=snapshot.planning_day.date.isoformat(),
        day_label=day_label(snapshot.planning_day),
        rule_count=len(rows),
        must_count=sum(1 for r in rows if r.necessity == "must"),
        off_today_count=snapshot.suspended_constraint_count,
        off_today_reason=snapshot.planning_day.day_type.value,
        groups=group_rows(rows),
        suspended=[
            SuspendedRow(uid=r.uid, name=r.name, reason=r.suspended_reason)
            for r in rows
            if r.suspended_reason is not None
        ],
        shown_with=shown_with_of(snapshot),
        first_shown_with=seed,
    )
```

Add `"ContextPanel", "SuspendedRow", "context_panel", "shown_with_of"` to `__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_context.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/stage_context.py tests/unit/test_stage_context.py
git commit -m "feat(slack): the context panel, built from the snapshot alone (#266)"
```

---

### Task 3: `context_fold` with count truncation

**Files:**
- Modify: `src/fateforger/slack_bot/stage_context.py`
- Test: `tests/unit/test_stage_context.py`

**Interfaces:**
- Consumes: Task 1's `rank_rows`, `group_rows`, `primary_anchor`; `SLACK_MAX_MODAL_BLOCKS`.
- Produces: `SteerVerb = Literal["steer_not_today", "steer_wrong", "restore"]`; `FoldRow(uid, name, necessity, applies, also: list[str], suspended_reason, verbs: list[SteerVerb])`; `FoldGroup(name, rows)`; `ContextFold(session_key, expected_revision, day, day_label, groups, off_today_count, off_today_reason, truncated: tuple[int, int] | None)`; `context_fold(snapshot, first_shown_with) -> ContextFold`; `fold_block_count(fold) -> int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_stage_context.py`:

```python
from fateforger.slack_bot.messages import SLACK_MAX_MODAL_BLOCKS
from fateforger.slack_bot.stage_context import context_fold, fold_block_count


def test_the_fold_lists_every_rule_once_with_its_other_anchors() -> None:
    rows = [_row("c1", GYM, BREAKFAST), _row("c2", GYM), _row("c3", BREAKFAST, DINNER)]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    by_uid = {r.uid: (g.name, r.also) for g in fold.groups for r in g.rows}
    assert by_uid["c1"] == ("breakfast", ["gym"])
    assert by_uid["c3"] == ("breakfast", ["dinner"])
    assert by_uid["c2"] == ("gym", [])


def test_verbs_depend_on_whether_the_row_is_suspended() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    fold = context_fold(_snapshot(rows, [_suspend("c2")]), first_shown_with=None)
    verbs = {r.uid: r.verbs for g in fold.groups for r in g.rows}
    assert verbs["c1"] == ["steer_not_today", "steer_wrong"]
    assert verbs["c2"] == ["restore"]


def test_the_fold_truncates_whole_groups_past_the_modal_cap() -> None:
    # 60 groups of 2 rows = 60 headings + 120 rows + 1 footer, far past 100.
    rows = [_row(f"c{i}", (f"a{i // 2}", f"anchor{i // 2}")) for i in range(120)]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    assert fold_block_count(fold) <= SLACK_MAX_MODAL_BLOCKS
    kept = sum(len(g.rows) for g in fold.groups)
    assert fold.truncated == (120 - kept, 60 - len(fold.groups))
    assert fold.groups[0].rows[0].uid == "c0"  # the top-ranked group survives


def test_a_fitting_fold_is_not_truncated() -> None:
    rows = [_row(f"c{i}", GYM) for i in range(41)]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    assert fold.truncated is None
    assert fold_block_count(fold) == 1 + 41 + 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_context.py -v -k fold`
Expected: FAIL with `ImportError: cannot import name 'context_fold'`.

- [ ] **Step 3: Add the fold model and builder**

Append to `stage_context.py`, before `__all__`. Import `SLACK_MAX_MODAL_BLOCKS` from
`.messages` at the top of the module (`messages.py` imports nothing from Slack; the AST
guard's forbidden set does not name it).

```python
SteerVerb = Literal["steer_not_today", "steer_wrong", "restore"]


class FoldRow(_Frozen):
    uid: str
    name: str
    necessity: Necessity
    applies: Applies | None
    #: The rule's other anchor names, for the "also" tag.
    also: list[str]
    suspended_reason: str | None
    verbs: list[SteerVerb]


class FoldGroup(_Frozen):
    name: str | None
    rows: list[FoldRow]


class ContextFold(_Frozen):
    session_key: str
    expected_revision: int
    day: str
    day_label: str
    groups: list[FoldGroup]
    off_today_count: int
    off_today_reason: str
    #: (rules, groups) dropped so the view fits the modal cap; None when it fit.
    truncated: tuple[int, int] | None = None


#: One heading section per group, one section per row, one footer section
#: for the memory-side suspensions.
_FOLD_FOOTER_BLOCKS = 1


def fold_block_count(fold: ContextFold) -> int:
    return sum(1 + len(g.rows) for g in fold.groups) + _FOLD_FOOTER_BLOCKS + (
        1 if fold.truncated else 0
    )


def _fold_row(row: RankedRow, primary: tuple[str, str] | None) -> FoldRow:
    verbs: list[SteerVerb] = (
        ["restore"] if row.suspended_reason is not None else ["steer_not_today", "steer_wrong"]
    )
    return FoldRow(
        uid=row.uid,
        name=row.name,
        necessity=row.necessity,
        applies=row.applies,
        also=[name for uid, name in row.anchors if primary is None or uid != primary[0]],
        suspended_reason=row.suspended_reason,
        verbs=verbs,
    )


def context_fold(
    snapshot: PlanningSessionSnapshot, first_shown_with: frozenset[str] | None
) -> ContextFold:
    if snapshot.planning_day is None:
        raise ValueError("a context fold needs a locked planning day")
    rows = rank_rows(snapshot, first_shown_with)
    sizes: Counter[str] = Counter(a_uid for row in rows for a_uid, _ in row.anchors)
    by_uid = {row.uid: row for row in rows}
    groups: list[FoldGroup] = []
    for group in group_rows(rows):
        groups.append(
            FoldGroup(
                name=group.name,
                rows=[
                    _fold_row(by_uid[uid], primary_anchor(by_uid[uid], sizes))
                    for uid in group.uids
                ],
            )
        )
    # Whole groups from the tail, until the view fits. Never a sliced row.
    budget = SLACK_MAX_MODAL_BLOCKS - _FOLD_FOOTER_BLOCKS
    kept: list[FoldGroup] = []
    used = 0
    for group in groups:
        cost = 1 + len(group.rows)
        if used + cost > budget - 1 and kept:  # leave one block for the "+N" line
            break
        kept.append(group)
        used += cost
    dropped_groups = groups[len(kept):]
    truncated = (
        (sum(len(g.rows) for g in dropped_groups), len(dropped_groups))
        if dropped_groups
        else None
    )
    return ContextFold(
        session_key=snapshot.session_key,
        expected_revision=snapshot.revision,
        day=snapshot.planning_day.date.isoformat(),
        day_label=day_label(snapshot.planning_day),
        groups=kept,
        off_today_count=snapshot.suspended_constraint_count,
        off_today_reason=snapshot.planning_day.day_type.value,
        truncated=truncated,
    )
```

Add `"ContextFold", "FoldGroup", "FoldRow", "SteerVerb", "context_fold", "fold_block_count"`
to `__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_context.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Break the cap on purpose**

Set `budget = 10_000` temporarily, run `test_the_fold_truncates_whole_groups_past_the_modal_cap`,
watch it fail, restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/stage_context.py tests/unit/test_stage_context.py
git commit -m "feat(slack): the context fold: every rule once, a steer verb set per row, whole-group truncation (#266)"
```

---

### Task 4: `DenyControl` on decided assumptions

**Files:**
- Modify: `src/fateforger/slack_bot/stage_cards.py`
- Test: `tests/unit/test_stage_cards.py`

**Interfaces:**
- Consumes *(Phase 1)*: `DecidedItem.filed_by`, `PlannerAssumption.filed_by`.
- Produces: `DenyControl(kind="deny_assumption", assumption_id: str)` in the `Control` union; `DecidedItem.controls: list[Control] = []`; `_decided` attaches `[DenyControl(assumption_id=...)]` to every assumption item and `[]` to facts.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_stage_cards.py`:

```python
def test_every_decided_assumption_carries_a_deny_control_and_facts_do_not() -> None:
    from fateforger.slack_bot.stage_cards import DenyControl

    card = _map(
        AwaitingUser(requirement_id="skeleton.requested_activity", question="q", why_needed="w"),
        _snapshot(),
    )
    by_ref = {item.ref: item for item in card.decided}
    assert by_ref["a-1"].controls == [DenyControl(assumption_id="a-1")]
    assert by_ref["activity-1"].controls == []
```

(`_map` is the module's existing helper that calls `map_outcome` with the fixture's
pending candidates, actor, session key, channel and thread.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_cards.py -v -k deny`
Expected: FAIL with `ImportError: cannot import name 'DenyControl'`.

- [ ] **Step 3: Add the control and the field**

In `stage_cards.py`, after `CancelControl`:

```python
class DenyControl(_Frozen):
    """Withdraw one assumption, the planner's or the user's. The kernel removes
    it and re-opens the cell it satisfied (`DenyAssumption`)."""

    kind: Literal["deny_assumption"] = "deny_assumption"
    assumption_id: str
```

Add `DenyControl,` to the `Control` union. `DecidedItem` is declared before `Control`, so
give it a forward reference and rebuild:

```python
class DecidedItem(_Frozen):
    text: str
    kind: Literal["assumption", "fact"]
    ref: str
    filed_by: Literal["planner", "user"] | None = None   # Phase 1
    #: What can be done to this item from the card. Drawn as one overflow.
    controls: list["Control"] = Field(default_factory=list)
```

After the `Control` alias: `DecidedItem.model_rebuild()`.

In `_decided`, the assumption comprehension gains `controls=[DenyControl(assumption_id=assumption.assumption_id)]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_cards.py -v`
Expected: PASS (the new test and every existing one; `as_receipt` still drops
card-level controls only, and a receipt's decided items keep their `controls` field but
Task 6's renderer draws no overflow on a receipt).

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/stage_cards.py tests/unit/test_stage_cards.py
git commit -m "feat(slack): every decided assumption carries a deny control (#263)"
```

---

### Task 5: `steer_not_today` as a button decision

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_intents.py`
- Test: `tests/unit/test_timeboxing_intents_steer.py`

**Interfaces:**
- Consumes *(Phase 1)*: `ArtifactActionMeta.decision` already holding `deny_assumption` and `restore`; `assumption_id`, `constraint_uid` fields with their validators; `suspension_fact_id`; `FactKind.SUSPENDED_CONSTRAINT`.
- Produces: `ArtifactActionMeta.decision` value `"steer_not_today"`, requiring `constraint_uid`, optional `note: str | None`; `intent_from_artifact_action` binds it to `ProvidePlanningFacts(facts=[PlanningFact(fact_id=suspension_fact_id(uid), kind=SUSPENDED_CONSTRAINT, value={"uid": uid, "reason": "not today", "note": note}, source="user")])`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_timeboxing_intents_steer.py
"""A steer press from the fold lands on the same fact id a typed 'not today'
does, so a second press is a no-op and restore deletes one id."""

from __future__ import annotations

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    ProvidePlanningFacts,
    suspension_fact_id,
)
from fateforger.slack_bot.timeboxing_intents import intent_from_artifact_action


def _meta(**extra) -> dict:
    return {"session_key": "C1:1.0", "expected_revision": 3, "decision": "steer_not_today", **extra}


def test_a_not_today_press_files_the_suspension_fact_at_its_stable_id() -> None:
    envelope = intent_from_artifact_action(_meta(constraint_uid="c-gym"))
    assert envelope is not None
    assert isinstance(envelope.intent, ProvidePlanningFacts)
    (fact,) = envelope.intent.facts
    assert fact.fact_id == suspension_fact_id("c-gym")
    assert fact.kind is FactKind.SUSPENDED_CONSTRAINT
    assert fact.value == {"uid": "c-gym", "reason": "not today", "note": None}
    assert fact.source == "user"


def test_this_is_wrong_is_not_today_plus_a_note() -> None:
    envelope = intent_from_artifact_action(_meta(constraint_uid="c-gym", note="this is wrong"))
    (fact,) = envelope.intent.facts
    assert fact.value["note"] == "this is wrong"


def test_a_not_today_press_without_a_uid_is_unreadable() -> None:
    assert intent_from_artifact_action(_meta()) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_intents_steer.py -v`
Expected: FAIL, `intent_from_artifact_action` returns `None` (the `Literal` refuses the decision).

- [ ] **Step 3: Extend the meta and the table**

In `ArtifactActionMeta`: add `"steer_not_today"` to the `decision` `Literal`; add
`note: str | None = Field(default=None, min_length=1)`; in the validator, after the
`deny_assumption` check Phase 1 added:

```python
        if self.decision == "steer_not_today" and self.constraint_uid is None:
            raise ValueError("a steer press names the rule it suspends")
```

In `intent_from_artifact_action`, before the `back` branch:

```python
    elif meta.decision == "steer_not_today":
        uid = cast(str, meta.constraint_uid)
        # The same id the typed path files (Phase 1 Task 8), so a press and
        # a sentence cannot suspend one rule twice.
        intent = ProvidePlanningFacts(
            facts=[
                PlanningFact(
                    fact_id=suspension_fact_id(uid),
                    kind=FactKind.SUSPENDED_CONSTRAINT,
                    value={"uid": uid, "reason": "not today", "note": meta.note},
                    source="user",
                )
            ]
        )
```

Import `suspension_fact_id` from `session_contracts`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_timeboxing_intents_steer.py tests/unit/test_timeboxing_intents*.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_intents.py tests/unit/test_timeboxing_intents_steer.py
git commit -m "feat(slack): a Not today press files the suspension fact at its stable id (#262)"
```

---

### Task 6: Renderers, action ids, and Block Kit validation in tests

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_cards.py`, `pyproject.toml`
- Test: `tests/unit/test_render_context_surfaces.py`, `tests/unit/test_render_stage_card.py`

**Interfaces:**
- Consumes: Tasks 2 and 3's `ContextPanel`, `ContextFold`; Task 4's `DecidedItem.controls`, `DenyControl`; Task 5's `steer_not_today` meta; *(Phase 1)* `restore` and `deny_assumption` metas.
- Produces: `FF_TIMEBOX_SHOW_RULES_ACTION_ID = "ff_timebox_show_rules"`, `FF_TIMEBOX_STEER_ACTION_ID = "ff_timebox_steer"`, `FF_TIMEBOX_DECIDED_ACTION_ID = "ff_timebox_decided"`; `render_context_panel(panel) -> SlackBlockMessage`; `render_context_fold(fold) -> dict` (a `modal` view); `render_stage_card` draws an overflow on decided items with controls (never on a receipt) and the free-association hint under `asking`.

- [ ] **Step 1: Add `blockkit` to the dev group**

In `pyproject.toml` under `[tool.poetry.group.dev.dependencies]` add `blockkit = "^2.1.3"`, then
`uv pip install blockkit==2.1.3` into `.venv` (see memory: `poetry install` is not run in
worktrees; `uv sync` is). Verify: `.venv/bin/python -c "import blockkit; print('ok')"`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_render_context_surfaces.py
"""Structure and counts only, plus Block Kit validity through `blockkit`:
a block Slack would refuse fails here, not as a 400 in the thread."""

from __future__ import annotations

import json
from datetime import date

from blockkit import Message, Modal
from blockkit.core import FieldValidationError  # noqa: F401 - surfaced by .build()

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    suspension_fact_id,
)
from fateforger.slack_bot.messages import SLACK_MAX_BLOCKS, SLACK_MAX_MODAL_BLOCKS
from fateforger.slack_bot.stage_context import context_fold, context_panel
from fateforger.slack_bot.timeboxing_cards import (
    FF_TIMEBOX_SHOW_RULES_ACTION_ID,
    FF_TIMEBOX_STEER_ACTION_ID,
    render_context_fold,
    render_context_panel,
)
from fateforger.slack_bot.timeboxing_intents import ArtifactActionMeta


def _day() -> PlanningDay:
    return PlanningDay.lock_default(value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1)


def _rows(n: int, anchors_per: int = 2) -> list[dict]:
    return [
        {
            "uid": f"c{i}",
            "name": f"Rule number {i} with a normal length name",
            "necessity": "must" if i % 3 == 0 else "should",
            "anchors": [{"uid": f"a{(i + k) % 24}", "name": f"anchor {(i + k) % 24}"} for k in range(anchors_per)],
            "fade": (i % 10) / 10,
        }
        for i in range(n)
    ]


def _snapshot(rows: list[dict], suspend: list[str] = ()) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        applicable_constraints=rows,
        suspended_constraint_count=1,
        facts=[
            PlanningFact(
                fact_id=suspension_fact_id(uid),
                kind=FactKind.SUSPENDED_CONSTRAINT,
                value={"uid": uid, "reason": "not today"},
                source="user",
            )
            for uid in suspend
        ],
    )


def _validated_message(blocks: list[dict]) -> None:
    # blockkit validates on .build(): a section over 3000 chars, an overflow
    # over 5 options, a button label over 75 chars raise FieldValidationError.
    Message(blocks=[_as_block(b) for b in blocks]).build()


def _text(node: dict):
    from blockkit import Text

    return Text(type=node["type"], text=node["text"])


def _element(node: dict):
    from blockkit import Button, Option, Overflow

    if node["type"] == "button":
        return Button(text=_text(node["text"]), action_id=node["action_id"], value=node.get("value"),
                      style=node.get("style"))
    if node["type"] == "overflow":
        return Overflow(action_id=node["action_id"],
                        options=[Option(text=_text(o["text"]), value=o["value"]) for o in node["options"]])
    raise AssertionError(f"unexpected element {node['type']}")


def _as_block(block: dict):
    """Rebuild one rendered block as blockkit objects so its validators run."""
    from blockkit import Actions, Context, Divider, Section

    kind = block["type"]
    if kind == "divider":
        return Divider()
    if kind == "context":
        return Context(elements=[_text(e) for e in block["elements"]])
    if kind == "actions":
        return Actions(elements=[_element(e) for e in block["elements"]])
    accessory = block.get("accessory")
    return Section(text=_text(block["text"]), accessory=_element(accessory) if accessory else None)


def test_the_panel_is_two_blocks_with_a_show_rules_control() -> None:
    panel = context_panel(_snapshot(_rows(41), suspend=["c3"]), first_shown_with=None)
    message = render_context_panel(panel)
    assert len(message.blocks) == 2
    accessory = message.blocks[0]["accessory"]
    assert accessory["action_id"] == FF_TIMEBOX_SHOW_RULES_ACTION_ID
    meta = json.loads(accessory["value"])
    assert (meta["session_key"], meta["expected_revision"]) == ("C1:1.0", 4)
    _validated_message(message.blocks)


def test_the_fold_is_a_modal_under_the_cap_with_a_steer_menu_per_row() -> None:
    fold = context_fold(_snapshot(_rows(41), suspend=["c3"]), first_shown_with=None)
    view = render_context_fold(fold)
    assert view["type"] == "modal"
    assert len(view["blocks"]) <= SLACK_MAX_MODAL_BLOCKS
    rows = [b for b in view["blocks"] if b.get("accessory", {}).get("type") == "overflow"]
    assert len(rows) == 41
    for row in rows:
        assert row["accessory"]["action_id"] == FF_TIMEBOX_STEER_ACTION_ID
        for option in row["accessory"]["options"]:
            ArtifactActionMeta.model_validate_json(option["value"])  # every pick decodes
    suspended = [r for r in rows if r["accessory"]["options"][0]["text"]["text"] == "Restore"]
    assert len(suspended) == 1
    assert "~" in suspended[0]["text"]["text"]
    Modal(title=view["title"]["text"], close="Close", blocks=[_as_block(b) for b in view["blocks"]]).build()


def test_the_fold_says_how_many_it_dropped() -> None:
    fold = context_fold(_snapshot(_rows(160, anchors_per=1)), first_shown_with=None)
    view = render_context_fold(fold)
    assert len(view["blocks"]) <= SLACK_MAX_MODAL_BLOCKS
    assert view["blocks"][-1]["text"]["text"].startswith("+")
```

Append to `tests/unit/test_render_stage_card.py`:

```python
def test_a_decided_assumption_draws_an_overflow_and_a_receipt_does_not() -> None:
    from fateforger.slack_bot.stage_cards import DecidedItem, DenyControl, StageCard, stage
    from fateforger.slack_bot.timeboxing_cards import FF_TIMEBOX_DECIDED_ACTION_ID, render_stage_card

    card = StageCard(
        stage=stage(1),
        session_key="C1:1.0",
        expected_revision=4,
        decided=[
            DecidedItem(text="assumed x", kind="assumption", ref="a-1", filed_by="user",
                        controls=[DenyControl(assumption_id="a-1")]),
            DecidedItem(text="wanted y", kind="fact", ref="f-1"),
        ],
    )
    live = render_stage_card(card).blocks
    overflows = [b for b in live if b.get("accessory", {}).get("type") == "overflow"]
    assert len(overflows) == 1
    assert overflows[0]["accessory"]["action_id"] == FF_TIMEBOX_DECIDED_ACTION_ID
    (option,) = overflows[0]["accessory"]["options"]
    meta = json.loads(option["value"])
    assert (meta["decision"], meta["assumption_id"]) == ("deny_assumption", "a-1")
    receipt = render_stage_card(card.as_receipt("answered")).blocks
    assert not any(b.get("accessory") for b in receipt)


def test_asking_carries_the_free_association_hint() -> None:
    from fateforger.slack_bot.stage_cards import Asking, StageCard, stage
    from fateforger.slack_bot.timeboxing_cards import render_stage_card

    card = StageCard(stage=stage(1), session_key="C1:1.0", expected_revision=4,
                     asking=Asking(requirement_id="elicit.body.unclear", question="q", why_needed="w"))
    blocks = render_stage_card(card).blocks
    hints = [b for b in blocks if b["type"] == "context" and "anything else" in b["elements"][0]["text"]]
    assert len(hints) == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_render_context_surfaces.py tests/unit/test_render_stage_card.py -v`
Expected: FAIL with `ImportError: cannot import name 'render_context_panel'`.

- [ ] **Step 4: Action ids and the panel renderer**

In `timeboxing_cards.py`, beside the other ids:

```python
FF_TIMEBOX_SHOW_RULES_ACTION_ID = "ff_timebox_show_rules"
FF_TIMEBOX_STEER_ACTION_ID = "ff_timebox_steer"
FF_TIMEBOX_DECIDED_ACTION_ID = "ff_timebox_decided"
```

Import `ContextFold`, `ContextPanel`, `FoldRow` from `.stage_context` and `DenyControl`
from `.stage_cards`; `SLACK_MAX_MODAL_BLOCKS` from `.messages`.

```python
_NECESSITY_LABEL = {"must": "must", "should": "should"}
_APPLIES_LABEL = {"every_day": "every day", "some_days": "some days", "dated": "dated"}


def _row_tags(row: FoldRow) -> str:
    tags = [_NECESSITY_LABEL[row.necessity]]
    if row.suspended_reason is not None:
        tags.append(f"you said: {row.suspended_reason}")
    elif row.applies is not None:
        tags.append(_APPLIES_LABEL[row.applies])
    if row.also:
        tags.append("also " + ", ".join(row.also))
    return " · ".join(tags)


def _off_today_line(count: int, reason: str) -> str:
    if count == 0:
        return ""
    noun = "rule" if count == 1 else "rules"
    return f" · {count} {noun} off today because it is a {reason} day"


def render_context_panel(panel: ContextPanel) -> SlackBlockMessage:
    """Two blocks. Counts and group names are the only variable text, so the
    panel never grows and is safe to edit in place for the whole session."""

    summary = " · ".join(
        f"*{g.name if g.name is not None else 'no anchor'}* {len(g.uids)}" for g in panel.groups
    )
    head = (
        f"*1/5 · Constraints — what I know about a {panel.day_label}*\n"
        f"{panel.rule_count} rules apply ({panel.must_count} must, "
        f"{panel.rule_count - panel.must_count} should)"
        f"{_off_today_line(panel.off_today_count, panel.off_today_reason)}\n{summary}"
    )
    blocks: list[dict] = [
        {
            "type": "section",
            "block_id": "ff_timebox_context_panel",
            "text": {"type": "mrkdwn", "text": head[:SLACK_MAX_BLOCK_TEXT_CHARS]},
            "accessory": _nav_button(
                FF_TIMEBOX_SHOW_RULES_ACTION_ID,
                "Show rules",
                artifact_action_value(
                    session_key=panel.session_key,
                    expected_revision=panel.expected_revision,
                    decision="advance",  # never sent: show_rules is a host action; the value binds the session
                    artifact=None,
                ),
            ),
        }
    ]
    if panel.suspended:
        names = ", ".join(f"{s.name} (you said: {s.reason})" for s in panel.suspended)
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_Off for this session: {names}._"[:SLACK_MAX_BLOCK_TEXT_CHARS]}],
            }
        )
    else:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "_Nothing set aside for this session._"}]}
        )
    return SlackBlockMessage(text=head.splitlines()[0][:SLACK_MAX_TEXT_CHARS], blocks=blocks)
```

The Show rules value is an `ArtifactActionMeta` so the handler can read the session
key and revision the same way every other press does; its `decision` is never executed
(Task 9 opens a modal instead of calling `intent_from_artifact_action`).

- [ ] **Step 5: The fold renderer**

```python
def _steer_option(fold: ContextFold, row: FoldRow, verb: str) -> dict:
    label = {"steer_not_today": "Not today", "steer_wrong": "This is wrong", "restore": "Restore"}[verb]
    fields = {
        "session_key": fold.session_key,
        "expected_revision": fold.expected_revision,
        "constraint_uid": row.uid,
    }
    if verb == "steer_wrong":
        fields.update(decision="steer_not_today", note="this is wrong")
    elif verb == "steer_not_today":
        fields.update(decision="steer_not_today")
    else:
        fields.update(decision="restore")
    return {
        "text": {"type": "plain_text", "text": label},
        "value": ArtifactActionMeta.model_validate(fields).model_dump_json(),
    }


def render_context_fold(fold: ContextFold) -> dict:
    """A modal view: one heading per group, one row per rule with its menu."""

    blocks: list[dict] = []
    for group in fold.groups:
        blocks.append(_section(f"*{group.name if group.name is not None else 'no anchor'}*"))
        for row in group.rows:
            name = f"~{row.name}~" if row.suspended_reason is not None else row.name
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{name}  _{_row_tags(row)}_"[:SLACK_MAX_BLOCK_TEXT_CHARS]},
                    "accessory": {
                        "type": "overflow",
                        "action_id": FF_TIMEBOX_STEER_ACTION_ID,
                        "options": [_steer_option(fold, row, verb) for verb in row.verbs],
                    },
                }
            )
    if fold.truncated:
        rules, groups = fold.truncated
        blocks.append(_section(f"+{rules} rules in {groups} more groups — say the rule's name to steer it"))
    blocks.append(_section(f"*what today is not*{_off_today_line(fold.off_today_count, fold.off_today_reason) or ' · nothing is off today'}"))
    assert len(blocks) <= SLACK_MAX_MODAL_BLOCKS, len(blocks)
    return {
        "type": "modal",
        "callback_id": "ff_timebox_context_fold",
        "private_metadata": artifact_action_value(
            session_key=fold.session_key, expected_revision=fold.expected_revision, decision="advance", artifact=None
        ),
        "title": {"type": "plain_text", "text": f"Rules for {fold.day}"[:24]},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": blocks,
    }
```

The title cap of 24 is Slack's for modal titles; an ISO date fits. `private_metadata`
carries the session so Task 9's refresh knows which fold to rebuild.

- [ ] **Step 6: The decided overflow and the hint in `render_stage_card`**

Replace the decided line in `render_stage_card`:

```python
    if card.decided:
        blocks.append(_section("*Decided*"))
        shown = card.decided[:STAGE_LIST_CAP]
        for item in shown:
            block = _section(f"• {item.text}")
            if item.controls and card.done is None:
                block["accessory"] = {
                    "type": "overflow",
                    "action_id": FF_TIMEBOX_DECIDED_ACTION_ID,
                    "options": [_decided_option(card, control) for control in item.controls],
                }
            blocks.append(block)
        rest = len(card.decided) - len(shown)
        if rest > 0:
            blocks.append(_section(f"_+{rest} more_"))
```

with

```python
def _decided_option(card: StageCard, control: Control) -> dict:
    if isinstance(control, DenyControl):
        return {
            "text": {"type": "plain_text", "text": "Deny"},
            "value": ArtifactActionMeta.model_validate(
                {
                    "session_key": card.session_key,
                    "expected_revision": card.expected_revision,
                    "decision": "deny_assumption",
                    "assumption_id": control.assumption_id,
                }
            ).model_dump_json(),
        }
    raise ValueError(f"no decided option for {control.kind}")
```

After the `asking` section (before the option buttons), add the hint:

```python
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "_Or tell me anything else about tomorrow; I will file it where it belongs._"}],
            }
        )
```

The decided block count is now `1 + min(len, 8) + 1`, so the turn card's maximum is
header 1 + decided 10 + divider 1 + asking 2 + hint 1 + options 1 + gate 1 + nav 1 +
typing hint 1 = 19, under 40. Update the docstring of `render_stage_card` with that sum.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_render_context_surfaces.py tests/unit/test_render_stage_card.py tests/unit/test_stage_cards.py -v`
Expected: PASS. If `blockkit` refuses a block, the refusal names the field; fix the
renderer, not the test.

- [ ] **Step 8: Break the validator on purpose**

Temporarily change the fold row's `text` slice to `[:4000]` and pad a fixture name to
3500 characters; `blockkit` must raise on the section. Restore.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml poetry.lock src/fateforger/slack_bot/timeboxing_cards.py tests/unit/test_render_context_surfaces.py tests/unit/test_render_stage_card.py
git commit -m "feat(slack): render the context panel, the fold modal and the decided overflow; Block Kit validated in tests (#266)"
```

---

### Task 7: The registry keeps one panel per session current

**Files:**
- Modify: `src/fateforger/slack_bot/stage_card_registry.py`
- Test: `tests/unit/test_stage_card_registry.py`

**Interfaces:**
- Consumes: Task 2's `ContextPanel`, `context_panel`, `shown_with_of`; Task 6's `render_context_panel`.
- Produces: `ShownPanel(channel, ts, panel)`; `StageCardRegistry.remember_panel(session_key, *, channel, ts, panel)`; `.panel_shown(session_key) -> ShownPanel | None`; `.forget` also drops the panel; `async sync_panel(client, *, session_key, snapshot, channel, thread_ts, logger) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_stage_card_registry.py`:

```python
from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    suspension_fact_id,
)


class _PostingClient(_Client):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(fail=fail)
        self.posts: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posts.append(dict(payload))
        return {"ok": True, "ts": f"200.{len(self.posts)}"}


def _snapshot_with(rows: list[str], *, day: date = date(2026, 9, 8), suspend: list[str] = ()):
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=day, timezone="Europe/Amsterdam", lock_revision=1),
        applicable_constraints=[
            {"uid": uid, "name": f"rule {uid}", "necessity": "must", "anchors": [], "fade": None} for uid in rows
        ],
        facts=[
            PlanningFact(fact_id=suspension_fact_id(u), kind=FactKind.SUSPENDED_CONSTRAINT,
                         value={"uid": u, "reason": "not today"}, source="user")
            for u in suspend
        ],
    )


async def _sync(registry, client, snapshot):
    await registry.sync_panel(
        client, session_key="C1:1.0", snapshot=snapshot, channel="C1", thread_ts="1.0",
        logger=logging.getLogger(__name__),
    )


@pytest.mark.asyncio
async def test_the_first_sync_posts_the_panel_and_remembers_its_ts() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    assert [p["thread_ts"] for p in client.posts] == ["1.0"]
    shown = registry.panel_shown("C1:1.0")
    assert shown is not None and (shown.ts, shown.thread_ts) == ("200.1", "1.0")
    assert shown.panel.first_shown_with == frozenset({"c1"})


@pytest.mark.asyncio
async def test_an_unchanged_row_set_does_nothing() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    await _sync(registry, client, _snapshot_with(["c1"]))
    assert len(client.posts) == 1 and client.updates == []


@pytest.mark.asyncio
async def test_a_suspension_edits_the_panel_in_place_and_keeps_first_shown_with() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1", "c2"]))
    await _sync(registry, client, _snapshot_with(["c1", "c2"], suspend=["c2"]))
    assert len(client.posts) == 1
    assert [u["ts"] for u in client.updates] == ["200.1"]
    assert registry.panel_shown("C1:1.0").panel.first_shown_with == frozenset({"c1", "c2"})


@pytest.mark.asyncio
async def test_a_day_change_receipts_the_old_panel_and_posts_a_new_one() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    await _sync(registry, client, _snapshot_with(["c1", "c9"], day=date(2026, 9, 9)))
    assert [u["ts"] for u in client.updates] == ["200.1"]
    assert "superseded" in client.updates[0]["blocks"][0]["text"]["text"]
    assert len(client.posts) == 2
    assert registry.panel_shown("C1:1.0").panel.first_shown_with == frozenset({"c1", "c9"})


@pytest.mark.asyncio
async def test_a_failed_edit_is_logged_and_the_record_stays() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    client._fail = True
    await _sync(registry, client, _snapshot_with(["c1"], suspend=["c1"]))  # no raise
    assert registry.panel_shown("C1:1.0").ts == "200.1"


@pytest.mark.asyncio
async def test_no_locked_day_means_no_panel() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    snapshot = _snapshot_with(["c1"]).model_copy(update={"planning_day": None})
    await _sync(registry, client, snapshot)
    assert client.posts == [] and registry.panel_shown("C1:1.0") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_card_registry.py -v -k panel`
Expected: FAIL with `AttributeError: 'StageCardRegistry' object has no attribute 'sync_panel'`.

- [ ] **Step 3: Add the record and `sync_panel`**

In `stage_card_registry.py`, import `ContextPanel`, `context_panel`, `shown_with_of`
from `.stage_context` and `render_context_panel` from `.timeboxing_cards`.

```python
@dataclass(frozen=True, slots=True)
class ShownPanel:
    channel: str
    #: The panel message itself.
    ts: str
    #: The session thread it was posted into; a modal press has no message
    #: to read this from, so the record keeps it.
    thread_ts: str
    panel: ContextPanel
```

In `StageCardRegistry.__init__`: `self._panels: dict[str, ShownPanel] = {}`. Methods:

```python
    def remember_panel(
        self, session_key: str, *, channel: str, ts: str, thread_ts: str, panel: ContextPanel
    ) -> None:
        self._panels[session_key] = ShownPanel(channel=channel, ts=ts, thread_ts=thread_ts, panel=panel)

    def panel_shown(self, session_key: str) -> ShownPanel | None:
        return self._panels.get(session_key)

    def forget(self, session_key: str) -> None:
        self._shown.pop(session_key, None)
        self._panels.pop(session_key, None)

    async def sync_panel(
        self, client, *, session_key: str, snapshot, channel: str, thread_ts: str, logger
    ) -> None:
        """Post the panel once, edit it when its rows change, replace it on a
        day change. Best-effort: the turn is saved before this runs, and a
        Slack failure here is logged, never raised."""

        if snapshot.planning_day is None:
            return
        previous = self._panels.get(session_key)
        day = snapshot.planning_day.date.isoformat()
        if previous is not None and previous.panel.day == day:
            if previous.panel.shown_with == shown_with_of(snapshot):
                return
            panel = context_panel(snapshot, previous.panel.first_shown_with)
            message = render_context_panel(panel)
            try:
                await client.chat_update(
                    channel=previous.channel, ts=previous.ts, text=message.text, blocks=message.blocks
                )
            except Exception as exc:  # noqa: BLE001 - presentation never owns the turn
                logger.warning(
                    "could not update the context panel session_key=%s ts=%s error_type=%s error=%s",
                    session_key, previous.ts, type(exc).__name__, exc,
                )
                return
            self._panels[session_key] = ShownPanel(
                channel=previous.channel, ts=previous.ts, thread_ts=previous.thread_ts, panel=panel
            )
            return
        if previous is not None:
            # A different day: the old panel is history, and says so.
            old = render_context_panel(previous.panel)
            head = old.blocks[0]["text"]["text"].splitlines()[0] + "  —  superseded"
            receipt = [{"type": "section", "text": {"type": "mrkdwn", "text": head}}]
            try:
                await client.chat_update(channel=previous.channel, ts=previous.ts, text=head, blocks=receipt)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "could not receipt the superseded context panel session_key=%s error_type=%s error=%s",
                    session_key, type(exc).__name__, exc,
                )
        panel = context_panel(snapshot, None)
        message = render_context_panel(panel)
        try:
            posted = await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts, text=message.text, blocks=message.blocks
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not post the context panel session_key=%s error_type=%s error=%s",
                session_key, type(exc).__name__, exc,
            )
            return
        # slack_sdk's AsyncSlackResponse supports .get like a dict; the tests' fake returns a dict.
        ts = str(posted.get("ts") or "")
        if ts:
            self._panels[session_key] = ShownPanel(channel=channel, ts=ts, thread_ts=thread_ts, panel=panel)
```

Add `"ShownPanel"` to `__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_card_registry.py -v`
Expected: PASS, the six new tests and the existing ones.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/stage_card_registry.py tests/unit/test_stage_card_registry.py
git commit -m "feat(slack): the registry keeps one context panel per session current (#266)"
```

---

### Task 8: The turn syncs the panel

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` (`_run_adaptive_timebox_turn`, right after `_stage_cards.transition(...)`)
- Test: `tests/unit/test_stage_panel_in_the_turn.py`

**Interfaces:**
- Consumes: Task 7's `sync_panel`; the existing `_run_adaptive_timebox_turn` locals `current` (the post-turn snapshot), `outcome`, `client`, `session_key`, `card_channel`, `card_thread_ts`, `logger`.
- Produces: after a non-failed turn, `await _stage_cards.sync_panel(client, session_key=..., snapshot=current, channel=card_channel, thread_ts=card_thread_ts, logger=logger)`, called **before** `transition` when the registry has no panel yet (so the panel sits above the first card) and **after** it otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_stage_panel_in_the_turn.py
"""Driven through `_run_adaptive_timebox_turn`, like the receipt tests: the
panel is posted once, above the first card, and edited when the rows change."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    AwaitingUser,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    suspension_fact_id,
)
from fateforger.slack_bot.stage_card_registry import StageCardRegistry


class _Client:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posts.append(dict(payload))
        return {"ok": True, "ts": f"300.{len(self.posts)}"}

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


def _snapshot(suspend: list[str] = ()) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=5,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1),
        applicable_constraints=[{"uid": "c1", "name": "r", "necessity": "must", "anchors": [], "fade": None}],
        facts=[
            PlanningFact(fact_id=suspension_fact_id(u), kind=FactKind.SUSPENDED_CONSTRAINT,
                         value={"uid": u, "reason": "not today"}, source="user")
            for u in suspend
        ],
    )


def _wire(monkeypatch, *, snapshots: list[PlanningSessionSnapshot]):
    outcome = AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="w")
    remaining = list(snapshots)

    class Kernel:
        async def turn(self, request, progress):
            return outcome

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    class Runtime:
        timeboxing_session_store = Repo()

    async def derive(*a, **k):
        return ProvidePlanningFacts(facts=[PlanningFact(fact_id="f", kind=FactKind.REQUESTED_ACTIVITY, value="x", source="user")])

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", derive)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    registry = StageCardRegistry()
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    return Runtime(), registry


@pytest.mark.asyncio
async def test_the_panel_is_posted_once_above_the_first_card_then_edited(monkeypatch) -> None:
    runtime, registry = _wire(monkeypatch, snapshots=[_snapshot(), _snapshot(suspend=["c1"])])
    client = _Client()
    kwargs = dict(runtime=runtime, client=client, logger=logging.getLogger(__name__),
                  session_key="C1:1.0", actor_user_id="U1", channel_id="C1", thread_ts="1.0")
    await handlers._run_adaptive_timebox_turn(interaction_id="i1", text="gym at 18", **kwargs)
    assert registry.panel_shown("C1:1.0") is not None
    first_panel_ts = registry.panel_shown("C1:1.0").ts
    await handlers._run_adaptive_timebox_turn(interaction_id="i2", text="not today for that", **kwargs)
    assert registry.panel_shown("C1:1.0").ts == first_panel_ts
    assert any(u["ts"] == first_panel_ts for u in client.updates)
```

Read `_run_adaptive_timebox_turn`'s actual signature before writing `kwargs`; the
receipt tests in `test_stage_receipts_in_the_turn.py` call it and are the reference.
Match their call exactly.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_panel_in_the_turn.py -v`
Expected: FAIL, `registry.panel_shown("C1:1.0") is None`.

- [ ] **Step 3: Call `sync_panel` from the turn**

In `_run_adaptive_timebox_turn`, around the existing `await _stage_cards.transition(...)`:

```python
    panel_first = (
        not isinstance(outcome, (TurnFailed, Cancelled))
        and _stage_cards.panel_shown(session_key) is None
    )
    if panel_first:
        await _stage_cards.sync_panel(
            client, session_key=session_key, snapshot=current,
            channel=card_channel, thread_ts=card_thread_ts, logger=logger,
        )
    await _stage_cards.transition(...)   # unchanged
    if not panel_first and not isinstance(outcome, (TurnFailed, Cancelled)):
        await _stage_cards.sync_panel(
            client, session_key=session_key, snapshot=current,
            channel=card_channel, thread_ts=card_thread_ts, logger=logger,
        )
```

On `Cancelled`, the existing code already forgets the session's card; `forget` now drops
the panel too (Task 7).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_stage_panel_in_the_turn.py tests/unit/test_stage_receipts_in_the_turn.py -v`
Expected: PASS. The receipt tests' `_Client` has no `chat_postMessage`; if they now fail
on that, add the method to their `_Client` returning `{"ok": True, "ts": "9.9"}`, which is
the honest fixture for a turn that posts a panel.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_stage_panel_in_the_turn.py tests/unit/test_stage_receipts_in_the_turn.py
git commit -m "feat(slack): a Stage 1 turn posts the context panel once and keeps it current (#262)"
```

---

### Task 9: Handlers: open the fold, route its picks, refresh it

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py`
- Test: `tests/unit/test_fold_modal_handlers.py`

**Interfaces:**
- Consumes: Task 3's `context_fold`; Task 6's `render_context_fold`, action ids; Task 7's `panel_shown`; the existing `_handle_timebox_artifact_action(runtime, client, logger, value, channel_id, thread_ts, actor_user_id, interaction_id)` and `_card_interaction_id`.
- Produces: `_handle_show_rules(runtime, client, logger, *, body) -> None`; `_handle_fold_pick(runtime, client, logger, *, body) -> None`; `_handle_decided_pick(runtime, client, logger, *, body) -> None`; three `app.action` registrations.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fold_modal_handlers.py
"""A press on Show rules opens the fold from durable state; an overflow pick
inside it takes the same path as a card button and refreshes the modal."""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import PlanningDay, PlanningSessionSnapshot
from fateforger.slack_bot.timeboxing_cards import FF_TIMEBOX_STEER_ACTION_ID
from fateforger.slack_bot.timeboxing_intents import artifact_action_value


class _Client:
    def __init__(self) -> None:
        self.opened: list[dict] = []
        self.updated: list[dict] = []
        self.ephemeral: list[dict] = []

    async def views_open(self, **payload):
        self.opened.append(dict(payload))
        return {"ok": True}

    async def views_update(self, **payload):
        self.updated.append(dict(payload))
        return {"ok": True}

    async def chat_postEphemeral(self, **payload):
        self.ephemeral.append(dict(payload))
        return {"ok": True}


def _snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0", revision=5, owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1),
        applicable_constraints=[{"uid": "c1", "name": "r", "necessity": "must", "anchors": [], "fade": None}],
    )


def _runtime(snapshot):
    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return snapshot

    class Runtime:
        timeboxing_session_store = Repo()

    return Runtime()


def _press_body(value: str, *, in_view: bool = False) -> dict:
    body = {
        "trigger_id": "T1",
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "message": {"ts": "1.0"},
        "actions": [{"action_id": FF_TIMEBOX_STEER_ACTION_ID, "selected_option": {"value": value}, "value": None}],
    }
    if in_view:
        body["view"] = {"id": "V1", "private_metadata": artifact_action_value(session_key="C1:1.0", expected_revision=5, decision="advance", artifact=None)}
        body["container"] = {"type": "view", "view_id": "V1"}
        body.pop("channel")
        body.pop("message")
    return body


@pytest.mark.asyncio
async def test_show_rules_opens_the_fold_from_the_snapshot() -> None:
    client = _Client()
    value = artifact_action_value(session_key="C1:1.0", expected_revision=5, decision="advance", artifact=None)
    body = {"trigger_id": "T1", "user": {"id": "U1"}, "channel": {"id": "C1"}, "message": {"ts": "1.0"},
            "actions": [{"action_id": "ff_timebox_show_rules", "value": value}]}
    await handlers._handle_show_rules(_runtime(_snapshot()), client, logging.getLogger(__name__), body=body)
    (opened,) = client.opened
    assert opened["trigger_id"] == "T1"
    assert opened["view"]["type"] == "modal"
    assert any(b.get("accessory", {}).get("type") == "overflow" for b in opened["view"]["blocks"])


@pytest.mark.asyncio
async def test_a_failed_open_tells_the_user_to_press_again() -> None:
    client = _Client()

    async def boom(**payload):
        raise RuntimeError("expired trigger")

    client.views_open = boom
    value = artifact_action_value(session_key="C1:1.0", expected_revision=5, decision="advance", artifact=None)
    body = {"trigger_id": "T1", "user": {"id": "U1"}, "channel": {"id": "C1"}, "message": {"ts": "1.0"},
            "actions": [{"action_id": "ff_timebox_show_rules", "value": value}]}
    await handlers._handle_show_rules(_runtime(_snapshot()), client, logging.getLogger(__name__), body=body)
    assert len(client.ephemeral) == 1


@pytest.mark.asyncio
async def test_a_fold_pick_reaches_the_artifact_action_path_and_refreshes_the_modal(monkeypatch) -> None:
    client = _Client()
    seen: list[str] = []

    async def fake_handle(*, runtime, client, logger, value, channel_id, thread_ts, actor_user_id, interaction_id):
        seen.append(json.loads(value)["decision"])

    monkeypatch.setattr(handlers, "_handle_timebox_artifact_action", fake_handle)
    value = json.dumps({"schema_version": 1, "session_key": "C1:1.0", "expected_revision": 5,
                        "decision": "steer_not_today", "constraint_uid": "c1"})
    await handlers._handle_fold_pick(_runtime(_snapshot()), client, logging.getLogger(__name__),
                                     body=_press_body(value, in_view=True))
    assert seen == ["steer_not_today"]
    (updated,) = client.updated
    assert updated["view_id"] == "V1"
    assert updated["view"]["type"] == "modal"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_fold_modal_handlers.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_handle_show_rules'`.

- [ ] **Step 3: The three handlers**

In `handlers.py`, near `_handle_timebox_artifact_action`. The session key and channel
are read from the press value (an `ArtifactActionMeta`), never guessed; the thread ts a
modal press lacks is recovered from the registry's panel record, which is the message
the press came from.

```python
async def _fold_for(runtime, *, session_key: str, actor_user_id: str):
    from .stage_context import context_fold

    snapshot = await runtime.timeboxing_session_store.load_or_create(
        session_key, owner_user_id=actor_user_id
    )
    shown = _stage_cards.panel_shown(session_key)
    first = shown.panel.first_shown_with if shown is not None else None
    return context_fold(snapshot, first)


async def _handle_show_rules(runtime, client, logger, *, body) -> None:
    """Open the fold. Reads durable state, changes nothing."""
    from .timeboxing_cards import render_context_fold
    from .timeboxing_intents import ArtifactActionMeta

    action = (body.get("actions") or [{}])[0]
    trigger_id = body.get("trigger_id") or ""
    actor_user_id = (body.get("user") or {}).get("id") or ""
    try:
        meta = ArtifactActionMeta.model_validate_json(action.get("value") or "")
    except ValueError:
        logger.warning("show rules press carried unreadable metadata")
        return
    if not (trigger_id and actor_user_id):
        return
    try:
        fold = await _fold_for(runtime, session_key=meta.session_key, actor_user_id=actor_user_id)
        await client.views_open(trigger_id=trigger_id, view=render_context_fold(fold))
    except Exception as exc:  # noqa: BLE001 - a modal that did not open is not a failed session
        logger.warning(
            "could not open the context fold session_key=%s error_type=%s error=%s",
            meta.session_key, type(exc).__name__, exc,
        )
        channel_id = (body.get("channel") or {}).get("id") or ""
        if channel_id:
            await client.chat_postEphemeral(
                channel=channel_id, user=actor_user_id,
                text="Couldn't open the rules just now — press Show rules again.",
            )


def _pick_value(body) -> str:
    action = (body.get("actions") or [{}])[0]
    selected = action.get("selected_option") or {}
    return str(selected.get("value") or action.get("value") or "")


async def _handle_fold_pick(runtime, client, logger, *, body) -> None:
    """One overflow pick inside the modal: the same path as a card button,
    then the modal is redrawn so the row shows what just happened."""
    from .timeboxing_cards import render_context_fold
    from .timeboxing_intents import ArtifactActionMeta

    value = _pick_value(body)
    actor_user_id = (body.get("user") or {}).get("id") or ""
    try:
        meta = ArtifactActionMeta.model_validate_json(value)
    except ValueError:
        logger.warning("fold pick carried unreadable metadata")
        return
    shown = _stage_cards.panel_shown(meta.session_key)
    if shown is None or not actor_user_id:
        logger.warning("fold pick for a session with no panel session_key=%s", meta.session_key)
        return
    action = (body.get("actions") or [{}])[0]
    await _handle_timebox_artifact_action(
        runtime=runtime, client=client, logger=logger, value=value,
        channel_id=shown.channel, thread_ts=shown.thread_ts, actor_user_id=actor_user_id,
        interaction_id=_card_interaction_id(action, str(action.get("action_id") or ""), shown.thread_ts),
    )
    view_id = ((body.get("view") or {}).get("id")) or ((body.get("container") or {}).get("view_id")) or ""
    if not view_id:
        return
    try:
        fold = await _fold_for(runtime, session_key=meta.session_key, actor_user_id=actor_user_id)
        await client.views_update(view_id=view_id, view=render_context_fold(fold))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not refresh the context fold session_key=%s error_type=%s error=%s",
            meta.session_key, type(exc).__name__, exc,
        )


async def _handle_decided_pick(runtime, client, logger, *, body) -> None:
    """A Deny pick on a decided item: a card press by another element type."""
    value = _pick_value(body)
    channel_id = (body.get("channel") or {}).get("id") or ""
    message = body.get("message") or {}
    thread_ts = message.get("thread_ts") or message.get("ts") or ""
    actor_user_id = (body.get("user") or {}).get("id") or ""
    if not (channel_id and thread_ts and actor_user_id and value):
        return
    action = (body.get("actions") or [{}])[0]
    await _handle_timebox_artifact_action(
        runtime=runtime, client=client, logger=logger, value=value,
        channel_id=channel_id, thread_ts=thread_ts, actor_user_id=actor_user_id,
        interaction_id=_card_interaction_id(action, str(action.get("action_id") or ""), thread_ts),
    )
```

`shown.thread_ts` is the session thread (Task 7 records it beside the panel's own
`ts`); a press inside a modal carries no message, so this is the only place it can come
from.

Registrations, beside the artifact action ones:

```python
    @app.action(FF_TIMEBOX_SHOW_RULES_ACTION_ID)
    async def _on_show_rules(ack, body, client, logger):
        await ack()
        await _handle_show_rules(runtime, client, logger, body=body)

    @app.action(FF_TIMEBOX_STEER_ACTION_ID)
    async def _on_fold_pick(ack, body, client, logger):
        await ack()
        await _handle_fold_pick(runtime, client, logger, body=body)

    @app.action(FF_TIMEBOX_DECIDED_ACTION_ID)
    async def _on_decided_pick(ack, body, client, logger):
        await ack()
        await _handle_decided_pick(runtime, client, logger, body=body)
```

Import the three ids from `.timeboxing_cards`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_fold_modal_handlers.py tests/unit/test_stage_card_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py src/fateforger/slack_bot/stage_card_registry.py tests/unit/test_fold_modal_handlers.py tests/unit/test_stage_card_registry.py
git commit -m "feat(slack): Show rules opens the fold; a pick inside it is a card press and redraws the modal (#262)"
```

---

### Task 10: The walk: ten probes, one panel, one steer

**Files:**
- Test: `tests/e2e/test_stage1_panel_walk.py`

**Interfaces:**
- Consumes: Task 8's wiring; the `_wire` harness pattern from `tests/unit/test_stage_panel_in_the_turn.py`.

- [ ] **Step 1: Write the test**

```python
# tests/e2e/test_stage1_panel_walk.py
"""Ten probe turns leave ten cards and one panel in the thread; a suspension
edits the panel in place and appears on the next card's decided list."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    AwaitingUser,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    suspension_fact_id,
)
from fateforger.slack_bot.stage_card_registry import StageCardRegistry


class _Client:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posts.append(dict(payload))
        return {"ok": True, "ts": f"400.{len(self.posts)}"}

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


class _StubCard:
    def __init__(self, *a, **k): ...
    async def close(self): ...


def _snapshot(revision: int, suspend: bool) -> PlanningSessionSnapshot:
    facts = [PlanningFact(fact_id=f"e{i}", kind=FactKind.REQUESTED_ACTIVITY, value=f"x{i}", source="user") for i in range(revision)]
    if suspend:
        facts.append(PlanningFact(fact_id=suspension_fact_id("c1"), kind=FactKind.SUSPENDED_CONSTRAINT,
                                  value={"uid": "c1", "reason": "not today"}, source="user"))
    return PlanningSessionSnapshot(
        session_key="C1:1.0", revision=revision, owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1),
        applicable_constraints=[{"uid": "c1", "name": "r1", "necessity": "must", "anchors": [{"uid": "a", "name": "gym"}], "fade": None},
                                {"uid": "c2", "name": "r2", "necessity": "should", "anchors": [], "fade": 0.5}],
        facts=facts,
    )


@pytest.mark.asyncio
async def test_ten_probes_one_panel_one_steer(monkeypatch) -> None:
    snapshots = [_snapshot(i + 1, suspend=(i >= 6)) for i in range(10)]
    it = iter(snapshots)
    current = {"s": snapshots[0]}

    class Kernel:
        async def turn(self, request, progress):
            current["s"] = next(it, current["s"])
            return AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="w")

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return current["s"]

    class Runtime:
        timeboxing_session_store = Repo()

    async def derive(*a, **k):
        return ProvidePlanningFacts(facts=[PlanningFact(fact_id="f", kind=FactKind.REQUESTED_ACTIVITY, value="x", source="user")])

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", derive)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    registry = StageCardRegistry()
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    client = _Client()
    kwargs = dict(runtime=Runtime(), client=client, logger=logging.getLogger(__name__),
                  session_key="C1:1.0", actor_user_id="U1", channel_id="C1", thread_ts="1.0")
    for i in range(10):
        await handlers._run_adaptive_timebox_turn(interaction_id=f"i{i}", text=f"probe {i}", **kwargs)

    panel_ts = registry.panel_shown("C1:1.0").ts
    panels = [p for p in client.posts if p.get("blocks", [{}])[0].get("block_id") == "ff_timebox_context_panel"]
    assert len(panels) == 1
    panel_edits = [u for u in client.updates if u["ts"] == panel_ts]
    assert len(panel_edits) == 1  # the suspension, once
    assert "not today" in panel_edits[0]["blocks"][1]["elements"][0]["text"]
```

How the ten turn cards are posted depends on how `_run_adaptive_timebox_turn` returns
its message to the caller (it returns a `SlackBlockMessage` the route posts, or posts
through the progress card). Read it; assert on whichever surface the receipt tests
assert on, and keep the panel assertions above as written.

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/e2e/test_stage1_panel_walk.py -v`
Expected: PASS. If it passes first time, break it: make `sync_panel` re-post instead of
edit and watch `len(panels) == 1` fail. Restore.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_stage1_panel_walk.py
git commit -m "test(slack): ten probes leave one panel, one edit, ten cards (#266)"
```

---

### Task 11: Full suite, docs pointer, and the live check

**Files:**
- Modify: `docs/superpowers/specs/2026-09-04-stage-card-grammar-design.md` (status line)
- Modify: `src/fateforger/slack_bot/README.md` (one paragraph naming the three surfaces and the three action ids)

- [ ] **Step 1: Run the whole unit suite on the harness backend**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: PASS. Any failure in an existing test that asserted the decided section as
one bullet block (`_bullets("Decided", ...)`) is a real change in Task 6; update that
assertion to the per-item sections and say so in the commit.

- [ ] **Step 2: README paragraph**

Under the section that describes `stage_cards.py`, add:

> Stage 1 has three surfaces. The **turn card** (`StageCard`) is posted per kernel turn
> and receipted on the next. The **context panel** (`stage_context.context_panel`) is
> two blocks posted once per stage and edited in place by
> `StageCardRegistry.sync_panel`. The **fold** (`stage_context.context_fold`) is the
> modal behind the panel's *Show rules* button (`ff_timebox_show_rules`); an overflow
> pick in it (`ff_timebox_steer`) or on a decided item (`ff_timebox_decided`) goes
> through `intent_from_artifact_action` like any button.

- [ ] **Step 3: Live check, on a PR rebased on main**

Per the project's e2e rule (memory: *E2E means a PR rebased on main*): open the PR,
run the stock startup scripts, and in the Slack channel `C0AA6HC1RJL` start a session
for a working day, answer two probes, press *Show rules*, pick *Not today* on one rule,
confirm: one panel above the first probe card, the panel edited (not re-posted) after
the pick, the modal redrawn with the row struck through, the next card's decided list
naming the suspension. Post the four screenshots on the PR. Post as Hugo via
`SLACK_USER_TOKEN` (memory: *Driving a live Slack timeboxing session*).

- [ ] **Step 4: Spec status and commit**

Change the spec's first line block to add `**Status:** implemented on <branch>, live
walk <date>.` Commit:

```bash
git add docs/superpowers/specs/2026-09-04-stage-card-grammar-design.md src/fateforger/slack_bot/README.md
git commit -m "docs(slack): the three Stage 1 surfaces and their action ids"
```

---

## Self-review notes

- **Spec coverage.** Three surfaces: Tasks 1–3 (models, builders), 6 (renderers). Deny
  control: Task 4. `steer_not_today` press: Task 5. Registry panel record and
  `sync_panel`: Task 7. Turn wiring and panel-above-first-card: Task 8. Modal open,
  pick, refresh, ephemeral on failure: Task 9. Ordering keys 1–4: Task 1 (key 2 through
  `coverage_matrix`, key 3 through `fade`). Multi-anchor once + also: Tasks 1, 3.
  Count truncation: Task 3. Block Kit validation as a dev dependency: Task 6. AST guard:
  Task 1. E2e walk: Task 10. **Not covered, by decision:** `PromoteControl` (stated in
  File Structure). **Depends on Phase 1 Task 1c:** the applicability tag renders only
  when rows carry `applies`; `_row_tags` omits it otherwise, which is honest absence,
  not a fallback that decides anything.
- **Placeholders.** None; every step has its code. Task 8 and Task 10 tell the
  implementer to read `_run_adaptive_timebox_turn`'s signature rather than guess it.
- **Type consistency.** `first_shown_with: frozenset[str] | None` on both builders;
  `ShownPanel(channel, ts, thread_ts, panel)` in Tasks 7 and 9; the three action id
  names are identical in Tasks 6 and 9; `steer_not_today` meta fields (`constraint_uid`,
  `note`) match between Tasks 5 and 6.
