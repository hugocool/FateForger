# Required Blocks — Increment 2 (tmbx slug on read; the planner must place required kinds) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A rule with `requires_block` in memory makes the planner place a block of that kind on the candidate, with the slug written to the calendar, and a candidate missing one is refused while the planner can still fix it.

**Architecture:** tmbx renders `slug` as a ninth column of the handle table and validates its shape (#211). The host's context port turns the day's `requires_block` values into one typed fact, `REQUIRED_BLOCKS`; one catalog entry, `candidate.required_blocks`, is open whenever that fact lists a slug and is planner-owned so it can never become a question; the brief names each required slug with its rule; the harness publishes the required slugs to the submit tool, which refuses a candidate whose captured patch and rows carry none of them; the kernel repeats that check when it accepts the draft (#214). Spec: `docs/superpowers/specs/2026-09-04-required-blocks-design.md` §2 and §3.

**Tech Stack:** Python 3.12, pydantic v2, pytest (`uv run pytest`), tmbx (`src/tmbx`), the timeboxing kernel (`src/fateforger/agents/timeboxing`), the Slack host (`src/fateforger/slack_bot`).

## Global Constraints

- Work in worktree `.worktrees/required-blocks-kernel`, branch `feat/required-blocks-kernel`, off `origin/main` 0b5241e. Tests: `uv run pytest <files> -q -p no:cacheprovider` from the worktree root (the repo's conftest sets paths; `tests/memory` is not touched by this plan).
- **No keyword, substring or regex matching on user content, anywhere, tests included** (CLAUDE.md). `re` is banned across `src/tmbx` outright. Slug shape checks are character-class loops over an identifier the system mints, like `is_valid_handle`. Slug equality is set membership over registry slugs.
- A gap the planner owns may not become a user question: `illegal_user_blocker` already refuses it (`memory-policy.md:126`). Infeasibility is a typed blocker naming the conflict.
- The candidate's commit basis (`digest`, `snapshot`, `patch`, `rows`) is captured by the host from the planner's own `plan_apply`, never written by the model. Required-slug presence is read from that capture only.
- A failure stays loud and named: the submit refusal is `required_block_missing` and names the slug; the kernel's is `TurnFailed(code="required_block_missing")`.
- `REQUIRED_BLOCKS` is filed only when at least one active rule requires a kind; an empty day of requirements files no fact, and the requirement is then satisfied.
- Stage 3 (skeleton) never reads the calendar; nothing here changes the context port's skeleton branch.
- Every test asserts something that fails without the change. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File structure

| file | responsibility |
|---|---|
| `src/tmbx/core/render.py` | `COLUMNS` gains `slug`; `plan_rows` emits `slug` (empty string when unset); `render_plan` writes it |
| `src/tmbx/core/models.py` | `is_valid_slug` beside `is_valid_handle` |
| `src/tmbx/core/ops.py` | `_validate_add` / `_validate_touch` refuse a malformed slug |
| `src/tmbx/server.py` | `plan_apply` docstring: what `slug` is for |
| `src/fateforger/agents/timeboxing/kg_constraint_client.py` | `_row_from_view` passes `requires_block` |
| `src/fateforger/agents/timeboxing/session_contracts.py` | `FactKind.REQUIRED_BLOCKS` |
| `src/fateforger/slack_bot/timeboxing_host.py` | `planning_facts` files `REQUIRED_BLOCKS`; `required_blocks_fact` helper |
| `src/fateforger/agents/timeboxing/readiness.py` | `candidate.required_blocks` entry; `_is_satisfied` branch |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | `required_block_missing` on draft acceptance |
| `src/fateforger/agents/timeboxing/required_blocks.py` | **new** — `required_slugs(facts)`, `slugs_on_candidate(payload)`: the two set computations both checks share |
| `src/fateforger/slack_bot/harness_bridge.py` | brief sentence per required slug; publishes `FF_DSH_REQUIRED_BLOCKS_FILE` |
| `src/fateforger/slack_bot/planning_result_mcp.py` | `_refuse_missing_required_block` |
| `infra/dsh/profile/memory-policy.md` | the paragraph that makes the rule reachable by the model |
| tests | `tests/unit/tmbx/test_render.py`, `test_render_rows.py`, `test_ops.py`; `tests/unit/test_timeboxing_host.py`, `test_kg_constraint_client*.py` (new if absent), `test_timeboxing_readiness.py`, `test_adaptive_timeboxing.py`, `test_required_blocks.py` (new), `test_candidate_obligation_names_apply.py`, `test_submit_refuses_missing_required_block.py` (new) |

---

### Task 1: tmbx renders `slug` as the ninth column (#211)

**Files:**
- Modify: `src/tmbx/core/render.py` (`COLUMNS`, `plan_rows`, `render_plan`)
- Modify: `src/tmbx/server.py` (`plan_apply` docstring, after the `p` (timing) sentence)
- Test: `tests/unit/tmbx/test_render.py`, `tests/unit/tmbx/test_render_rows.py`

**Interfaces:**
- Produces: `COLUMNS == ("H", "own", "type", "summary", "ST", "ET", "mode", "dur", "slug")`; every row dict from `plan_rows` has key `"slug"` (the block's slug or `""`); the rendered header reads `blocks[N]{H,own,type,summary,ST,ET,mode,dur,slug}:` and each line ends with the slug field (empty when unset). Downstream: Task 7's submit check reads `rows[*]["slug"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/tmbx/test_render_rows.py`:

```python
def test_rows_carry_the_slug_and_the_table_renders_it_last():
    """#211: `Block.slug` was written to the calendar on every commit and shown to
    nobody, so a planner could not see a day already carried a `planning` block
    and would add a second. The slug is the kind a rule requires; it has to be
    visible where the planner patches."""
    from datetime import date
    from tmbx.core.models import ET, Block, FixedStart, Plan
    from tmbx.core.render import COLUMNS, plan_rows, render_plan

    plan = Plan(
        date=date(2026, 9, 7),
        blocks=[
            Block(uid="u-pl", h="PLN1", n="Plan tomorrow", t=ET.PR,
                  p=FixedStart(st="17:30:00", dur="PT20M"), anchor_source="constraint",
                  slug="planning"),
            Block(uid="u-dw", h="DW1", n="Deep work", t=ET.DW,
                  p=FixedStart(st="09:30:00", dur="PT1H30M"), anchor_source="user"),
        ],
    )
    rows = plan_rows(plan)
    assert COLUMNS[-1] == "slug"
    assert [r["slug"] for r in rows] == ["planning", ""]
    table = render_plan(plan).splitlines()
    assert table[0] == "blocks[2]{H,own,type,summary,ST,ET,mode,dur,slug}:"
    assert table[1].endswith(",planning")
    assert table[2].endswith(",")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/tmbx/test_render_rows.py -q -p no:cacheprovider -k slug`
Expected: FAIL — `COLUMNS[-1] == "dur"` / `KeyError: 'slug'`.

- [ ] **Step 3: Add the column**

In `src/tmbx/core/render.py`: `COLUMNS = ("H", "own", "type", "summary", "ST", "ET", "mode", "dur", "slug")`. In `plan_rows`, add `"slug": r.slug or "",` after `"dur"`. In `render_plan`, append `row["slug"]` to the joined list after `row["dur"]`. Add to `render_plan`'s docstring: "``slug`` is the recurring kind of block (``planning``, ``sleep``), shown so a planner can see a required kind is already on the day; empty when the block has none." Check `plan.resolve()`'s row objects expose `slug` (they are `Block`s or resolved rows carrying the block's fields — if the resolved row type lacks `slug`, read it from the block by handle: `{b.h: b.slug for b in plan.blocks}`).

- [ ] **Step 4: Update the four literal headers in `tests/unit/tmbx/test_render.py`** (lines ~169–193: `"blocks[1]{H,own,type,summary,ST,ET,mode,dur}:\n"` → `"blocks[1]{H,own,type,summary,ST,ET,mode,dur,slug}:\n"`, and each expected row gains a trailing `,`). Run the tmbx unit suite: `uv run pytest tests/unit/tmbx -q -p no:cacheprovider`. Any other test asserting a row's exact text (`test_server.py`, `test_service.py`) gets the same trailing field.

- [ ] **Step 5: Document it on the tool**

In `src/tmbx/server.py`, `plan_apply` docstring, after the `p` (timing) sentence:

```
        `slug` names the recurring KIND of block -- `planning`, `sleep` -- and is
        rendered as the last column of plan_read. Set it verbatim to the kind the
        brief says is required; leave it out for everything else. It is a
        lowercase word with hyphens; anything else is refused.
```

- [ ] **Step 6: Run and commit**

Run: `uv run pytest tests/unit/tmbx -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/tmbx/core/render.py src/tmbx/server.py tests/unit/tmbx/test_render.py tests/unit/tmbx/test_render_rows.py
git commit -m "feat(tmbx): the handle table shows each block's slug, so a required kind already on the day is visible (#211)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: tmbx refuses a malformed slug (shape only)

**Files:**
- Modify: `src/tmbx/core/models.py` (`is_valid_slug` after `is_valid_handle`)
- Modify: `src/tmbx/core/ops.py` (`_validate_add`, `_validate_touch`)
- Test: `tests/unit/tmbx/test_ops.py`

**Interfaces:**
- Produces: `is_valid_slug(value: str) -> bool` (lowercase ASCII letters and single hyphens, no leading/trailing/doubled hyphen, non-empty). `validate_patch` returns `"op {i}: slug 'X' must be lowercase letters with single hyphens"` for a bad slug on an add or update.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/tmbx/test_ops.py` (use that file's existing plan/patch helpers for a one-block plan; the snippet below builds its own if none fits):

```python
def test_a_malformed_slug_on_an_add_or_update_is_refused_by_shape():
    """Membership in the registry is the kernel's check; tmbx only knows the
    shape of an identifier it will write into the calendar."""
    from datetime import date
    from tmbx.core.models import ET, Block, FixedStart, Plan, is_valid_slug
    from tmbx.core.ops import Patch, validate_patch

    assert is_valid_slug("planning") and is_valid_slug("morning-routine")
    for bad in ("", "Planning", "plan ning", "-planning", "planning-", "plan--ning", "plan_ning"):
        assert not is_valid_slug(bad), bad

    plan = Plan(date=date(2026, 9, 7), blocks=[
        Block(uid="u1", h="DW1", n="Deep work", t=ET.DW,
              p=FixedStart(st="09:30:00", dur="PT1H"), anchor_source="user"),
    ])
    add = Patch.model_validate({"ops": [
        {"op": "add", "h": "PLN1", "n": "Plan", "t": "PR", "after": "END",
         "p": {"a": "ap", "dur": "PT20M"}, "slug": "Planning Session"},
    ]})
    errors = validate_patch(plan, add)
    assert any("slug" in e and "PLN1" in e for e in errors), errors
    update = Patch.model_validate({"ops": [{"op": "update", "h": "DW1", "slug": "deep_work"}]})
    errors = validate_patch(plan, update)
    assert any("slug" in e and "DW1" in e for e in errors), errors
    ok = Patch.model_validate({"ops": [{"op": "update", "h": "DW1", "slug": "deep-work"}]})
    assert not [e for e in validate_patch(plan, ok) if "slug" in e]
```

(If `Patch` lives under a different name or the `t` value `"PR"` is not in `ET`, use the file's existing helper and a type that is; the `after: "END"` is required for a first add.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/tmbx/test_ops.py -q -p no:cacheprovider -k malformed_slug`
Expected: FAIL — `ImportError: is_valid_slug`.

- [ ] **Step 3: Implement**

`src/tmbx/core/models.py`, after `is_valid_handle`:

```python
def is_valid_slug(value: str) -> bool:
    """True if ``value`` is a slug the calendar may carry: lowercase ASCII
    letters and single hyphens, no leading, trailing or doubled hyphen.

    Plain string predicates, like ``is_valid_handle`` -- the ban on ``re`` is
    absolute across ``src/tmbx``. This is the SHAPE of an identifier; whether
    the word is a registered kind is the host's question, not tmbx's.
    """
    if not value or value[0] == "-" or value[-1] == "-" or "--" in value:
        return False
    return all(("a" <= ch <= "z") or ch == "-" for ch in value)
```

`src/tmbx/core/ops.py`: import `is_valid_slug` beside `is_valid_handle`; in `_validate_add`, after the handle check:

```python
    if op.slug is not None and not is_valid_slug(op.slug):
        errors.append(
            f"op {index}: {op.h} slug {op.slug!r} must be lowercase letters with single hyphens"
        )
```

and in `_validate_touch`, after the `touched` check, for `UpdateBlock` only:

```python
    if isinstance(op, UpdateBlock) and op.slug is not None and not is_valid_slug(op.slug):
        errors.append(
            f"op {index}: {op.h} slug {op.slug!r} must be lowercase letters with single hyphens"
        )
```

Export `is_valid_slug` in `models.py`'s `__all__` if one exists.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/tmbx -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/tmbx/core/models.py src/tmbx/core/ops.py tests/unit/tmbx/test_ops.py
git commit -m "feat(tmbx): a slug is validated by shape on add and update; membership stays the host's check (#211)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The host files a `REQUIRED_BLOCKS` fact from the day's rules

**Files:**
- Modify: `src/fateforger/agents/timeboxing/session_contracts.py` (`FactKind.REQUIRED_BLOCKS`)
- Modify: `src/fateforger/agents/timeboxing/kg_constraint_client.py` (`_row_from_view`)
- Create: `src/fateforger/agents/timeboxing/required_blocks.py`
- Modify: `src/fateforger/slack_bot/timeboxing_host.py` (`planning_facts`)
- Test: `tests/unit/test_timeboxing_host.py`, `tests/unit/test_required_blocks.py` (new)

**Interfaces:**
- Produces:
  ```python
  FactKind.REQUIRED_BLOCKS = "required_blocks"
  # required_blocks.py
  def required_blocks_value(constraints: Any) -> dict | None
      # {"slugs": ["planning"], "by_rule": {"planning": {"uid": "...", "name": "..."}}} or None when no row has requires_block
  def required_slugs(facts: Iterable[PlanningFact]) -> set[str]
      # union of value["slugs"] over facts of kind REQUIRED_BLOCKS
  def slugs_on_candidate(payload: Any) -> set[str]
      # slugs on add/update ops in payload["patch"]["ops"] ∪ payload["rows"][*]["slug"], non-empty strings only
  ```
  `planning_facts(...)` appends `PlanningFact(fact_id=f"required-blocks:{day}", kind=FactKind.REQUIRED_BLOCKS, value=<that dict>, source="constraint_memory")` when the value is not None. `_row_from_view` emits `"requires_block": view.requires_block`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_required_blocks.py`:

```python
"""The two set computations every required-block check shares (spec §2).

Both are arithmetic over identifiers the system minted: registry slugs on the
memory side, `slug` fields tmbx wrote on the tmbx side. Nothing here reads a
title.
"""
from __future__ import annotations

from fateforger.agents.timeboxing.required_blocks import (
    required_blocks_value,
    required_slugs,
    slugs_on_candidate,
)
from fateforger.agents.timeboxing.session_contracts import FactKind, PlanningFact


def _rows(*pairs):
    return [{"uid": uid, "name": name, "requires_block": rb} for uid, name, rb in pairs]


def test_the_fact_value_lists_each_required_slug_once_with_its_rule():
    value = required_blocks_value(_rows(
        ("c1", "Daily planning", "planning"),
        ("c2", "Work start", None),
        ("c3", "End of day planning", "planning"),
    ))
    assert value == {
        "slugs": ["planning"],
        "by_rule": {"planning": {"uid": "c1", "name": "Daily planning"}},
    }


def test_no_rule_requiring_a_kind_means_no_fact():
    assert required_blocks_value(_rows(("c2", "Work start", None))) is None
    assert required_blocks_value([]) is None
    assert required_blocks_value(None) is None


def test_required_slugs_reads_only_the_required_blocks_fact():
    facts = [
        PlanningFact(fact_id="f1", kind=FactKind.REQUIRED_BLOCKS,
                     value={"slugs": ["planning", "sleep"], "by_rule": {}}, source="constraint_memory"),
        PlanningFact(fact_id="f2", kind=FactKind.CALENDAR_SNAPSHOT,
                     value={"fetched": True, "blocks": 3}, source="calendar"),
    ]
    assert required_slugs(facts) == {"planning", "sleep"}
    assert required_slugs([facts[1]]) == set()


def test_slugs_on_a_candidate_come_from_ops_and_rows():
    payload = {
        "patch": {"ops": [
            {"op": "add", "h": "PLN1", "slug": "planning"},
            {"op": "update", "h": "DW1", "slug": "deep-work"},
            {"op": "remove", "h": "X1"},
            {"op": "add", "h": "Y1"},
        ]},
        "rows": [{"h": "SLP1", "slug": "sleep"}, {"h": "DW1", "slug": ""}, {"h": "Z1"}],
    }
    assert slugs_on_candidate(payload) == {"planning", "deep-work", "sleep"}
    assert slugs_on_candidate({}) == set()
    assert slugs_on_candidate(None) == set()
```

Append to `tests/unit/test_timeboxing_host.py`:

```python
def test_a_rule_requiring_a_kind_files_the_required_blocks_fact():
    from fateforger.agents.timeboxing.session_contracts import FactKind
    from fateforger.slack_bot.timeboxing_host import planning_facts

    facts = planning_facts(
        day="2026-09-07",
        calendar_snapshot={"ok": True, "blocks": 3},
        constraints=[
            {"uid": "c1", "name": "Daily planning", "requires_block": "planning"},
            {"uid": "c2", "name": "Work start", "requires_block": None},
        ],
    )
    required = [f for f in facts if f.kind is FactKind.REQUIRED_BLOCKS]
    assert len(required) == 1
    assert required[0].fact_id == "required-blocks:2026-09-07"
    assert required[0].source == "constraint_memory"
    assert required[0].value["slugs"] == ["planning"]
    assert required[0].value["by_rule"]["planning"] == {"uid": "c1", "name": "Daily planning"}


def test_no_required_kind_files_no_required_blocks_fact():
    from fateforger.agents.timeboxing.session_contracts import FactKind
    from fateforger.slack_bot.timeboxing_host import planning_facts

    facts = planning_facts(
        day="2026-09-07", calendar_snapshot={"ok": True, "blocks": 3},
        constraints=[{"uid": "c2", "name": "Work start", "requires_block": None}],
    )
    assert not [f for f in facts if f.kind is FactKind.REQUIRED_BLOCKS]


def test_the_memory_row_carries_requires_block():
    from types import SimpleNamespace
    from fateforger.agents.timeboxing.kg_constraint_client import _row_from_view

    view = SimpleNamespace(
        uid="c1", name="Daily planning", description="d",
        necessity=SimpleNamespace(value="should"), status=SimpleNamespace(value="proposed"),
        source=SimpleNamespace(value="user"), scope=SimpleNamespace(value="profile"),
        frame_slot=None, anchors=[], fade=None, applies="always", requires_block="planning",
    )
    assert _row_from_view(view)["requires_block"] == "planning"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_required_blocks.py tests/unit/test_timeboxing_host.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: required_blocks` / `AttributeError: REQUIRED_BLOCKS` / `KeyError: 'requires_block'`.

- [ ] **Step 3: Implement**

`session_contracts.py`, in `FactKind` after `REVISION_INSTRUCTION`:

```python
    #: The registered kinds of block the day's active rules say must be on the
    #: plan, as ``{"slugs": [...], "by_rule": {slug: {"uid", "name"}}}``.
    #: Filed by the host at candidate time from memory's ``requires_block``
    #: values; read by readiness (open while it lists any slug), by the brief
    #: (which names each one with its rule) and by the submit and acceptance
    #: checks. Never filed when no rule requires a kind.
    REQUIRED_BLOCKS = "required_blocks"
```

Create `src/fateforger/agents/timeboxing/required_blocks.py`:

```python
"""Required blocks: the set arithmetic every check shares (spec §2).

A rule in memory can say a block of a registered kind must be on the day
(`requires_block`, #212). Three places act on that -- the host filing a fact,
the submit tool refusing a candidate, the kernel accepting a draft -- and each
needs the same two sets: the slugs the day requires, and the slugs a candidate
carries. Both are unions over identifiers this system minted; nothing here
reads a title or a description.
"""
from __future__ import annotations

from typing import Any, Iterable

from fateforger.agents.timeboxing.session_contracts import FactKind, PlanningFact


def required_blocks_value(constraints: Any) -> dict[str, Any] | None:
    """The fact value for a day's rules, or None when no rule requires a kind.

    `by_rule` keeps the first rule that named each slug, so the brief can say
    "(from memory: <rule name>)" without a second join.
    """
    if not isinstance(constraints, list):
        return None
    by_rule: dict[str, dict[str, str]] = {}
    for row in constraints:
        if not isinstance(row, dict):
            continue
        slug = row.get("requires_block")
        if not isinstance(slug, str) or not slug:
            continue
        by_rule.setdefault(
            slug, {"uid": str(row.get("uid") or ""), "name": str(row.get("name") or "")}
        )
    if not by_rule:
        return None
    return {"slugs": sorted(by_rule), "by_rule": by_rule}


def required_slugs(facts: Iterable[PlanningFact]) -> set[str]:
    out: set[str] = set()
    for fact in facts:
        if fact.kind is not FactKind.REQUIRED_BLOCKS or not isinstance(fact.value, dict):
            continue
        slugs = fact.value.get("slugs")
        if isinstance(slugs, list):
            out.update(s for s in slugs if isinstance(s, str) and s)
    return out


def slugs_on_candidate(payload: Any) -> set[str]:
    """Slugs a candidate carries: on its add/update ops, and on the rows tmbx
    resolved for it. Both come from the captured `plan_apply`, never from prose."""
    if not isinstance(payload, dict):
        return set()
    out: set[str] = set()
    patch = payload.get("patch")
    ops = patch.get("ops") if isinstance(patch, dict) else None
    for op in ops or []:
        if isinstance(op, dict) and op.get("op") in ("add", "update"):
            slug = op.get("slug")
            if isinstance(slug, str) and slug:
                out.add(slug)
    for row in payload.get("rows") or []:
        if isinstance(row, dict):
            slug = row.get("slug")
            if isinstance(slug, str) and slug:
                out.add(slug)
    return out


__all__ = ["required_blocks_value", "required_slugs", "slugs_on_candidate"]
```

`kg_constraint_client.py`, in `_row_from_view`, after `"frame_slot": view.frame_slot,`: `"requires_block": getattr(view, "requires_block", None),`.

`timeboxing_host.py`, in `planning_facts`, turn the final `return [...]` into a list `facts = [...]`, then:

```python
    required = required_blocks_value(constraints)
    if required is not None:
        facts.append(
            PlanningFact(
                fact_id=f"required-blocks:{day}",
                kind=FactKind.REQUIRED_BLOCKS,
                value=required,
                source="constraint_memory",
            )
        )
    return facts
```

with `from fateforger.agents.timeboxing.required_blocks import required_blocks_value` at the top. Extend the docstring: "The third fact, `REQUIRED_BLOCKS`, is the one exception to 'presence only': readiness reads its slugs, so it is filed only when a rule requires a kind."

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_required_blocks.py tests/unit/test_timeboxing_host.py tests/unit/test_timeboxing_host_stage1_rows.py tests/unit/test_timeboxing_host_frame_from_corpus.py -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/fateforger/agents/timeboxing/session_contracts.py src/fateforger/agents/timeboxing/kg_constraint_client.py src/fateforger/agents/timeboxing/required_blocks.py src/fateforger/slack_bot/timeboxing_host.py tests/unit/test_required_blocks.py tests/unit/test_timeboxing_host.py
git commit -m "feat(timeboxing): the host files REQUIRED_BLOCKS from the day's rules that require a kind (#214)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: One catalog entry, `candidate.required_blocks`

**Files:**
- Modify: `src/fateforger/agents/timeboxing/readiness.py` (`_REQUIREMENTS`, `_is_satisfied`)
- Test: `tests/unit/test_timeboxing_readiness.py`

**Interfaces:**
- Produces: requirement `candidate.required_blocks` — `target_artifact=VALIDATED_CANDIDATE`, `satisfied_by=(FactKind.REQUIRED_BLOCKS,)`, `owner=PLANNER`, `hard=True`, `resolution="assume"`, `why_needed="a block of every kind the day's rules require must be on the plan"`, `question="A required block could not be placed. Which block should give way?"`, `stage=4`. Satisfied iff `required_slugs(snapshot.facts)` is empty. Appears in `planner_owned_gaps()` when open, so the brief lists it and `illegal_user_blocker` guards it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_timeboxing_readiness.py`:

```python
def _candidate_ready_snapshot(*extra: PlanningFact) -> PlanningSessionSnapshot:
    skeleton = PlanningArtifact.create(
        artifact_id="skeleton-1", kind=ArtifactKind.SKELETON, revision=1,
        payload={"markdown": "- Gym at 17:00"}, dependency_revisions={"planning_day": 1},
    )
    snapshot = _locked_snapshot(
        _fact(fact_id="cal", kind=FactKind.CALENDAR_SNAPSHOT, value={"fetched": True, "blocks": 0}),
        _fact(fact_id="con", kind=FactKind.ACTIVE_CONSTRAINTS, value={"fetched": True, "count": 1}),
        *extra,
    )
    return snapshot.model_copy(update={
        "artifacts": [skeleton],
        "approvals": [ArtifactApproval(
            artifact_id=skeleton.artifact_id, artifact_revision=skeleton.revision,
            artifact_digest=skeleton.digest, actor_user_id="U1", session_revision=1,
        )],
    })


def test_a_required_kind_opens_one_planner_owned_gap_that_is_never_a_question() -> None:
    """#214: the planner places a required block; it may not ask about it."""
    snapshot = _candidate_ready_snapshot(
        _fact(fact_id="req", kind=FactKind.REQUIRED_BLOCKS,
              value={"slugs": ["planning"], "by_rule": {"planning": {"uid": "c1", "name": "Daily planning"}}}),
    )
    report = TimeboxRequirements().evaluate(ArtifactKind.VALIDATED_CANDIDATE, snapshot)
    gap = report.by_id("candidate.required_blocks")
    assert gap.owner is RequirementOwner.PLANNER
    assert gap.resolution == "assume"
    assert gap.hard
    assert not gap.satisfied
    assert "candidate.required_blocks" in {g.requirement_id for g in report.planner_owned_gaps()}
    assert report.first_hard_user_blocker() is None


def test_no_required_kind_leaves_the_requirement_satisfied() -> None:
    report = TimeboxRequirements().evaluate(ArtifactKind.VALIDATED_CANDIDATE, _candidate_ready_snapshot())
    assert report.by_id("candidate.required_blocks").satisfied


def test_the_catalog_has_exactly_one_required_blocks_entry_however_many_kinds() -> None:
    """The count of entries does not grow with the number of tracked kinds."""
    from fateforger.agents.timeboxing.readiness import _REQUIREMENTS

    ids = [r.requirement_id for r in _REQUIREMENTS]
    assert ids.count("candidate.required_blocks") == 1
    two_kinds = _candidate_ready_snapshot(
        _fact(fact_id="req", kind=FactKind.REQUIRED_BLOCKS,
              value={"slugs": ["planning", "sleep"], "by_rule": {}}),
    )
    report = TimeboxRequirements().evaluate(ArtifactKind.VALIDATED_CANDIDATE, two_kinds)
    assert [g.requirement_id for g in report.gaps].count("candidate.required_blocks") == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_timeboxing_readiness.py -q -p no:cacheprovider -k required`
Expected: FAIL — `KeyError: 'candidate.required_blocks'`.

- [ ] **Step 3: Implement**

In `readiness.py`, add to `_REQUIREMENTS` after `candidate.concrete_placements`:

```python
    ArtifactRequirement(
        requirement_id="candidate.required_blocks",
        target_artifact=ArtifactKind.VALIDATED_CANDIDATE,
        satisfied_by=(FactKind.REQUIRED_BLOCKS,),
        owner=RequirementOwner.PLANNER,
        hard=True,
        why_needed="a block of every kind the day's rules require must be on the plan",
        resolution="assume",
        question="A required block could not be placed. Which block should give way?",
        stage=4,
    ),
```

In `_is_satisfied`, before the `if requirement.cell is not None:` line:

```python
        if requirement.requirement_id == "candidate.required_blocks":
            # One entry for every tracked kind (#214): open while the day's
            # rules require any kind at all, computed over the set rather than
            # hardcoded per kind. `satisfied_by` names the fact for the record;
            # presence is the wrong test here, because the fact lists what is
            # required and an empty requirement is satisfied by construction.
            return not required_slugs(snapshot.facts)
```

with `from fateforger.agents.timeboxing.required_blocks import required_slugs` at the top (no cycle: `required_blocks` imports only `session_contracts`).

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_timeboxing_readiness.py tests/unit/test_adaptive_timeboxing.py -q -p no:cacheprovider`
Expected: all PASS (existing kernel tests build candidates without a `REQUIRED_BLOCKS` fact, so the new entry is satisfied for them).

```bash
git add src/fateforger/agents/timeboxing/readiness.py tests/unit/test_timeboxing_readiness.py
git commit -m "feat(timeboxing): one planner-owned requirement covers every required kind on the day (#214)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The kernel refuses a candidate missing a required kind

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` (draft acceptance, after the "contradictory_artifact_updates" check and before `_invalidate`)
- Test: `tests/unit/test_adaptive_timeboxing.py`

**Interfaces:**
- Produces: `TurnFailed(code="required_block_missing", message="The plan is missing a required block: <slugs>.")` when the accepted draft is a `VALIDATED_CANDIDATE` and `required_slugs(snapshot.facts) - slugs_on_candidate(draft.payload)` is non-empty. Logged as `planner result refused reason=required_block_missing slugs=[...]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_adaptive_timeboxing.py` (reuse that file's `_incident_snapshot`, `_skeleton`, `_approval`, `_planning_day_artifact`, `_fact`, `_kernel`, `RecordedPlanner`, `RecordedContextPort`, `RecordingProgressSink`, `_with`, `TurnRequest`, `Advance`):

```python
def _required(slugs: list[str]) -> PlanningFact:
    return _fact("req-1", FactKind.REQUIRED_BLOCKS, {"slugs": slugs, "by_rule": {}})


def _candidate_result_with(ops: list[dict], rows: list[dict] = ()) -> PlanningResult:
    return PlanningResult(
        artifact_updates=[
            ArtifactDraft(
                kind=ArtifactKind.VALIDATED_CANDIDATE,
                payload={
                    "digest": "d" * 64,
                    "snapshot": {"event_ids": {}},
                    "patch": {"ops": ops},
                    "rows": list(rows),
                },
                dependency_revisions={"skeleton": 1},
            )
        ]
    )


def _candidate_context() -> RecordedContextPort:
    return RecordedContextPort(
        facts=(
            _fact("cal-1", FactKind.CALENDAR_SNAPSHOT, {"fetched": True, "blocks": 0}),
            _fact("con-1", FactKind.ACTIVE_CONSTRAINTS, {"fetched": True, "count": 1}),
            _required(["planning"]),
        )
    )


def _advance_from_approved_skeleton() -> tuple[InMemoryPlanningSessionRepository, TurnRequest]:
    skeleton = _skeleton()
    snapshot = _with(
        _incident_snapshot(),
        artifacts=[_planning_day_artifact(), skeleton],
        approvals=[_approval(_planning_day_artifact()), _approval(skeleton)],
    )
    request = TurnRequest(
        session_key="C1:1.0", interaction_id="adv-1", actor_user_id="U1",
        expected_revision=3, intent=Advance(),
    )
    return InMemoryPlanningSessionRepository([snapshot]), request


@pytest.mark.asyncio
async def test_a_candidate_without_the_required_kind_is_refused_by_name() -> None:
    """#214: a candidate that forgot the planning block must not be shown for
    approval; the refusal names the kind so the planner can fix it."""
    repo, request = _advance_from_approved_skeleton()
    planner = RecordedPlanner(_candidate_result_with(
        [{"op": "add", "h": "DW1", "n": "Deep work", "t": "DW", "p": {"a": "ap", "dur": "PT1H"}}]
    ))
    outcome = await _kernel(repo, planner, context=_candidate_context()).turn(
        request, progress=RecordingProgressSink()
    )
    assert isinstance(outcome, TurnFailed), outcome
    assert outcome.code == "required_block_missing"
    assert "planning" in outcome.message
    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert ArtifactKind.VALIDATED_CANDIDATE not in [a.kind for a in saved.artifacts]


@pytest.mark.asyncio
async def test_a_candidate_carrying_the_required_kind_on_an_op_is_accepted() -> None:
    repo, request = _advance_from_approved_skeleton()
    planner = RecordedPlanner(_candidate_result_with(
        [{"op": "add", "h": "PLN1", "n": "Plan tomorrow", "t": "PR",
          "p": {"a": "ap", "dur": "PT20M"}, "slug": "planning"}]
    ))
    outcome = await _kernel(repo, planner, context=_candidate_context()).turn(
        request, progress=RecordingProgressSink()
    )
    assert isinstance(outcome, AwaitingApproval), outcome
    assert outcome.artifact.kind is ArtifactKind.VALIDATED_CANDIDATE


@pytest.mark.asyncio
async def test_a_required_kind_already_on_the_day_satisfies_it_through_the_rows() -> None:
    """The block was on the calendar before the patch; tmbx's rows carry its slug."""
    repo, request = _advance_from_approved_skeleton()
    planner = RecordedPlanner(_candidate_result_with(
        [{"op": "add", "h": "DW1", "n": "Deep work", "t": "DW", "p": {"a": "ap", "dur": "PT1H"}}],
        rows=[{"h": "PLN1", "slug": "planning"}],
    ))
    outcome = await _kernel(repo, planner, context=_candidate_context()).turn(
        request, progress=RecordingProgressSink()
    )
    assert isinstance(outcome, AwaitingApproval), outcome
```

If `PlanningResult`/`ArtifactDraft`/`TurnFailed`/`AwaitingApproval` are not yet imported at the top of the test file, add them from `fateforger.agents.timeboxing.session_contracts` / `adaptive_timeboxing` as the file's other tests do.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_adaptive_timeboxing.py -q -p no:cacheprovider -k required_kind`
Expected: the first FAILS (`AwaitingApproval` where `TurnFailed` expected); the other two may already pass — that is fine, they pin the accept paths.

- [ ] **Step 3: Implement**

In `adaptive_timeboxing.py`, in the acceptance path, after the `contradictory_artifact_updates` refusal and before `updated = self._invalidate(snapshot, target)`:

```python
        draft = matching[0]
        if target is ArtifactKind.VALIDATED_CANDIDATE:
            # The submit tool already refuses this inside the turn, while the
            # planner can still fix it; this is the kernel's own copy of the
            # same set arithmetic, for a host that publishes no required-slug
            # file. A candidate missing a required kind must not reach the
            # user for approval (#214).
            missing = required_slugs(snapshot.facts) - slugs_on_candidate(draft.payload)
            if missing:
                logger.error(
                    "planner result refused reason=%s slugs=%s",
                    "required_block_missing",
                    sorted(missing),
                )
                return snapshot, TurnFailed(
                    code="required_block_missing",
                    message=(
                        "The plan is missing a required block: "
                        + ", ".join(sorted(missing))
                        + "."
                    ),
                )
```

and remove the later duplicate `draft = matching[0]` line. Import `required_slugs, slugs_on_candidate` from `fateforger.agents.timeboxing.required_blocks`.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_adaptive_timeboxing.py tests/unit/test_timeboxing_readiness.py -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/unit/test_adaptive_timeboxing.py
git commit -m "feat(timeboxing): the kernel refuses a candidate that lacks a required kind, by name (#214)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The brief names each required kind with its rule; the policy says it is placed, not asked

**Files:**
- Modify: `src/fateforger/slack_bot/harness_bridge.py` (`_planning_obligation`)
- Modify: `infra/dsh/profile/memory-policy.md` (after the "A gap you own may not become a user question." paragraph)
- Test: `tests/unit/test_candidate_obligation_names_apply.py`

**Interfaces:**
- Produces: for a `VALIDATED_CANDIDATE` brief whose facts carry `REQUIRED_BLOCKS`, the obligation text contains, per slug, the sentence: `A \`<slug>\` block is required today (from memory: <rule name>). Set \`slug: <slug>\` on it verbatim; place it from the day's other rules and record the time as an assumption on \`candidate.required_blocks\`.` The skeleton brief never carries it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_candidate_obligation_names_apply.py` (its `_brief(target)` helper builds a `PlanningBrief`; extend it with an optional `facts` argument if it has none, defaulting to `[]`):

```python
def test_the_candidate_obligation_names_each_required_kind_with_its_rule() -> None:
    """#214: existence is (from memory: …), time is an assumption -- two
    claims about one block, stated as two, or the model picks one."""
    from fateforger.agents.timeboxing.session_contracts import FactKind, PlanningFact

    brief = _brief(ArtifactKind.VALIDATED_CANDIDATE)
    brief = brief.model_copy(update={"facts": [
        *brief.facts,
        PlanningFact(
            fact_id="required-blocks:2026-09-07", kind=FactKind.REQUIRED_BLOCKS,
            value={"slugs": ["planning"], "by_rule": {"planning": {"uid": "c1", "name": "Daily timeboxing planning session"}}},
            source="constraint_memory",
        ),
    ]})
    text = harness_bridge._planning_obligation(brief)
    assert "A `planning` block is required today (from memory: Daily timeboxing planning session)." in text
    assert "Set `slug: planning` on it verbatim" in text
    assert "candidate.required_blocks" in text


def test_a_brief_without_required_kinds_says_nothing_about_them() -> None:
    text = harness_bridge._planning_obligation(_brief(ArtifactKind.VALIDATED_CANDIDATE))
    assert "is required today" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_candidate_obligation_names_apply.py -q -p no:cacheprovider`
Expected: the first FAILS on the missing sentence.

- [ ] **Step 3: Implement**

In `_planning_obligation`, after `payload_shape`:

```python
    # A rule saying a block must exist is placed, not asked about. The brief
    # says which kinds and which rule, and splits the two claims the block
    # will carry: existence is (from memory: …), the time is assumed (#214).
    required_lines = ""
    if brief.target_artifact is ArtifactKind.VALIDATED_CANDIDATE:
        by_rule: dict = {}
        for fact in brief.facts:
            if fact.kind is FactKind.REQUIRED_BLOCKS and isinstance(fact.value, dict):
                by_rule.update(fact.value.get("by_rule") or {})
        for slug in sorted(required_slugs(brief.facts)):
            rule = by_rule.get(slug) or {}
            name = str(rule.get("name") or rule.get("uid") or "a standing rule")
            required_lines += (
                f"\nA `{slug}` block is required today (from memory: {name}). "
                f"Set `slug: {slug}` on it verbatim; place it from the day's other "
                "rules and record the time as an assumption on "
                "`candidate.required_blocks`. A candidate without it is refused."
            )
```

and append `{required_lines}` to the returned f-string right after `{payload_shape}`. Import `FactKind` (if not already) and `required_slugs`.

`infra/dsh/profile/memory-policy.md`, after the paragraph ending "A gap you own may not become a user question.":

```
A rule that says a block of some kind must be on the day is placed, not asked
about. The brief names each such kind and the rule behind it; put a block of
that kind on the plan with `slug` set to that exact word, attribute its
existence to the rule and its time to your own assumption on
`candidate.required_blocks`. If nothing you can arrange fits it, that is the
typed infeasibility above, naming what it conflicts with -- never a question
about whether he wants it.
```

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_candidate_obligation_names_apply.py tests/unit/test_skeleton_payload_contract.py tests/unit/test_timeboxing_profile_contract.py -q -p no:cacheprovider`
Expected: all PASS. (`test_timeboxing_profile_contract` compares the repo's `cordis.patch.yml` with the deployed one; `memory-policy.md` is not part of that comparison — if a sibling test compares the policy file too, deploy it with the command that test prints, and say so in the report.)

```bash
git add src/fateforger/slack_bot/harness_bridge.py infra/dsh/profile/memory-policy.md tests/unit/test_candidate_obligation_names_apply.py
git commit -m "feat(planner): the brief names each required kind with its rule; the policy says it is placed, not asked (#214)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The submit tool refuses a candidate missing a required kind, inside the turn

**Files:**
- Modify: `src/fateforger/slack_bot/planning_result_mcp.py` (`REQUIRED_BLOCKS_FILE_ENV`, `_refuse_missing_required_block`, call site)
- Modify: `src/fateforger/slack_bot/harness_bridge.py` (publish the file beside `open-requirements.json`)
- Test: `tests/unit/test_submit_refuses_missing_required_block.py` (new), `tests/unit/test_submit_refuses_unknown_requirement.py` (pattern reference only)

**Interfaces:**
- Produces: `REQUIRED_BLOCKS_FILE_ENV = "FF_DSH_REQUIRED_BLOCKS_FILE"` — a JSON list of slugs the host writes per planning turn. `submit_planning_result` for a `validated_candidate` reads it, reads the captured candidate file (`CANDIDATE_OUTPUT_FILE_ENV`, shape `{"snapshot", "patch", "rows", ...}` as `validated_timebox_draft.read_validated_candidate` reads it), and raises `PlanningResultRefused` naming the missing slugs. Fails open when the required-slugs file is absent or empty (a host that does not publish it still has the kernel check); it never fails open when the file lists slugs.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_submit_refuses_missing_required_block.py`:

```python
"""A candidate without a required kind is refused while the planner can still
add it (#214, spec §2).

Same position as the captured-patch guard: `submit_planning_result` is callable
at any moment, so the refusal has to be inside the turn, and it names the slug
because a refusal the model cannot act on only burns a step. Presence is read
from the captured `plan_apply` (ops and rows), never from the artifact prose.
"""
import json

import pytest

from fateforger.slack_bot.planning_result_mcp import (
    PLANNING_RESULT_FILE_ENV,
    REQUIRED_BLOCKS_FILE_ENV,
    PlanningResultRefused,
    submit_planning_result,
)
from fateforger.slack_bot.validated_timebox_draft import CANDIDATE_OUTPUT_FILE_ENV


@pytest.fixture
def turn(tmp_path, monkeypatch):
    (tmp_path / "planning-result.json").touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(tmp_path / "planning-result.json"))
    monkeypatch.setenv(CANDIDATE_OUTPUT_FILE_ENV, str(tmp_path / "candidate.json"))
    monkeypatch.setenv(REQUIRED_BLOCKS_FILE_ENV, str(tmp_path / "required-blocks.json"))
    return tmp_path


def _captured(turn, ops, rows=()):
    (turn / "candidate.json").write_text(json.dumps({
        "version": 1, "snapshot": {"event_ids": {}}, "patch": {"ops": ops}, "rows": list(rows),
    }), encoding="utf-8")


def _require(turn, slugs):
    (turn / "required-blocks.json").write_text(json.dumps(slugs), encoding="utf-8")


def _submit():
    return submit_planning_result(
        target_artifact="validated_candidate", artifact={"blocks": []},
        assumptions=[], blockers=[],
    )


def test_a_candidate_missing_a_required_kind_is_refused_by_name(turn) -> None:
    _require(turn, ["planning"])
    _captured(turn, [{"op": "add", "h": "DW1", "slug": None}])
    with pytest.raises(PlanningResultRefused) as caught:
        _submit()
    assert "planning" in str(caught.value)
    assert "slug" in str(caught.value)


def test_a_candidate_with_the_kind_on_an_op_is_accepted(turn) -> None:
    _require(turn, ["planning"])
    _captured(turn, [{"op": "add", "h": "PLN1", "slug": "planning"}])
    _submit()


def test_a_kind_already_on_the_day_is_seen_through_the_rows(turn) -> None:
    _require(turn, ["planning"])
    _captured(turn, [{"op": "add", "h": "DW1"}], rows=[{"h": "PLN1", "slug": "planning"}])
    _submit()


def test_a_host_that_publishes_no_required_kinds_fails_open(turn, monkeypatch) -> None:
    monkeypatch.delenv(REQUIRED_BLOCKS_FILE_ENV)
    _captured(turn, [{"op": "add", "h": "DW1"}])
    _submit()


def test_an_empty_requirement_list_refuses_nothing(turn) -> None:
    _require(turn, [])
    _captured(turn, [{"op": "add", "h": "DW1"}])
    _submit()


def test_a_skeleton_is_never_checked_for_required_kinds(turn) -> None:
    _require(turn, ["planning"])
    submit_planning_result(
        target_artifact="skeleton", artifact={"markdown": "# Day", "reasoning": "r"},
        assumptions=[], blockers=[],
    )
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_submit_refuses_missing_required_block.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: REQUIRED_BLOCKS_FILE_ENV`.

- [ ] **Step 3: Implement the refusal**

In `planning_result_mcp.py`, beside `OPEN_REQUIREMENTS_FILE_ENV`:

```python
#: A JSON list of the registry slugs the day's rules require, written by the
#: host per planning turn so the submit tool can refuse a candidate that lacks
#: one while the planner still has steps left to add it (#214).
REQUIRED_BLOCKS_FILE_ENV = "FF_DSH_REQUIRED_BLOCKS_FILE"
```

After `_refuse_unapplied_candidate`:

```python
def _refuse_missing_required_block(*, target_artifact: str, artifact: Any) -> None:
    """A candidate that lacks a block of a required kind is refused here, by name.

    The host publishes the slugs the day's rules require; presence is read from
    the captured `plan_apply` -- the ops that added or updated a block with that
    slug, and the rows tmbx resolved (a block already on the day carries its
    slug there). Never from the artifact: a model-written claim of presence is
    the forged basis the captured-patch guard exists to refuse.

    Fails open when the host publishes nothing, like the open-requirements
    guard: the kernel repeats this check when it accepts the draft, so the only
    loss is the early correction. It never fails open when slugs are published.
    """
    if target_artifact != ArtifactKind.VALIDATED_CANDIDATE.value or artifact is None:
        return
    configured = os.environ.get(REQUIRED_BLOCKS_FILE_ENV, "").strip()
    if not configured:
        return
    try:
        published = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    required = {str(s) for s in published if isinstance(s, str) and s} if isinstance(published, list) else set()
    if not required:
        return
    captured = os.environ.get(CANDIDATE_OUTPUT_FILE_ENV, "").strip()
    try:
        payload = json.loads(Path(captured).read_text(encoding="utf-8")) if captured else {}
    except (OSError, ValueError):
        payload = {}
    missing = required - slugs_on_candidate(payload)
    if not missing:
        return
    raise PlanningResultRefused(
        "this candidate has no block of a kind the day requires: "
        + ", ".join(sorted(missing))
        + ". Add one with `plan_apply` and `slug` set to exactly that word, "
        "then submit again. The requirement is candidate.required_blocks; "
        "record its time as your assumption."
    )
```

Call it in `submit_planning_result` right after `_refuse_unapplied_candidate(...)`: `_refuse_missing_required_block(target_artifact=target_artifact, artifact=artifact)`. Import `slugs_on_candidate` from `fateforger.agents.timeboxing.required_blocks`.

- [ ] **Step 4: Publish the file from the harness**

In `harness_bridge.py`, in the `if planning_brief is not None:` block after `open_requirements` is written:

```python
            # The slugs the day's rules require, so the submit tool can refuse a
            # candidate that lacks one while the planner can still add it. From
            # the brief's own facts, so the server and the kernel cannot
            # disagree about what was required (#214).
            required_blocks = Path(workspace) / "required-blocks.json"
            required_blocks.write_text(
                json.dumps(sorted(required_slugs(planning_brief.facts))), encoding="utf-8"
            )
            child_env[REQUIRED_BLOCKS_FILE_ENV] = str(required_blocks)
```

Import `REQUIRED_BLOCKS_FILE_ENV` from `.planning_result_mcp` and `required_slugs` (already imported in Task 6). Add a unit test in `tests/unit/test_submit_refuses_missing_required_block.py` only if a harness-level test harness for the env handoff already exists in `tests/unit/test_submit_refuses_unknown_requirement.py`; otherwise cover the publish line by the e2e in Task 8.

- [ ] **Step 5: Run and commit**

Run: `uv run pytest tests/unit/test_submit_refuses_missing_required_block.py tests/unit/test_submit_refuses_unknown_requirement.py tests/unit/test_submit_refuses_unapplied_candidate.py -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/fateforger/slack_bot/planning_result_mcp.py src/fateforger/slack_bot/harness_bridge.py tests/unit/test_submit_refuses_missing_required_block.py
git commit -m "feat(planner): a candidate missing a required kind is refused inside the turn, by name (#214)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Whole suite, rebase, push, PR

- [ ] **Step 1: Full offline suite from the worktree root**

Run: `uv run pytest -q -p no:cacheprovider -m "not slow" 2>&1 | tail -3`
Expected: all pass except the pre-existing `tests/e2e/test_slack_handoff_flow.py::test_slack_handoff_sets_focus_and_forwards` (fails on `main` too).

- [ ] **Step 2: Rebase, rerun, push, PR**

```bash
git fetch origin && git rebase origin/main
uv run pytest -q -p no:cacheprovider -m "not slow" 2>&1 | tail -2
git push -u origin feat/required-blocks-kernel
gh pr create --base main --title "timeboxing: the planner must place every required kind; tmbx shows the slug (#211, #214, increment 2 of required blocks)" --body "..."
```

PR body: what lands per task; the two refusal sites and why both exist; the human checklist (Hugo runs one timeboxing session for a working day and sees a `planning` block on the 4/5 card with `slug=planning` in the journal's ops; on a weekend day none is demanded). End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Self-review

**Spec coverage.** §3 tmbx: render slug on read (Task 1), shape validation only (Task 2), write path unchanged (nothing to do). §2 kernel: `REQUIRED_BLOCKS` fact from the context port, arithmetic, skeleton branch untouched (Task 3); one catalog entry, open/satisfied over the set, `gym_placement` left alone (Task 4); brief text with the two attributions (Task 6); planner cannot ask — inherited via `illegal_user_blocker`, pinned by `first_hard_user_blocker() is None` in Task 4's test; submit refusal `required_block_missing` naming the slug (Task 7) and the kernel's copy (Task 5); policy paragraph (Task 6). §5's `required_block_missing` code is the kernel's `TurnFailed.code`; the submit tool raises `PlanningResultRefused` with the same wording, which is what the harness surfaces.

**Placeholders.** None; the PR body sketch in Task 8 is instructions to the implementer, not plan text with gaps.

**Type consistency.** `required_slugs(facts) -> set[str]`, `slugs_on_candidate(payload) -> set[str]`, `required_blocks_value(constraints) -> dict | None`, `FactKind.REQUIRED_BLOCKS`, `REQUIRED_BLOCKS_FILE_ENV`, requirement id `candidate.required_blocks`, fact id `required-blocks:<day>`, `TurnFailed.code == "required_block_missing"` — used identically across Tasks 3–7.
