# Stage 1 Elicitation Loop (Increment B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Stage 1 actually ask: three host-side judges write the coverage matrix the merged gate already reads, so the session probes what a good coach would ask and proposes to close only when nothing is uncovered.

**Architecture:** `PlacementJudge`, `CoverageJudge` and `ProbeJudge` follow `DayFrameJudge` — a model client in, one schema-bound call, raise on anything else. An `elicit()` orchestrator runs place → classify (parallel) → rank (existing arithmetic) → generate (top three, parallel) and returns one matrix fact plus probe drafts; the host's `resolve(SKELETON)` puts both on `PlanningContext`, and the kernel prefers the resolved probe over the catalog text. The gate, the consent flow and the cards are untouched. Quality is measured by ablation (delete a known fact, assert its cell is asked) and turns-to-`GateMet` with a non-contender simulated user.

**Tech Stack:** Python 3.11, pydantic v2 strict models, `autogen_core` `ChatCompletionClient` with `json_output=`, pytest (`-m "not slow"` for unit; `slow` for evals), OpenRouter via `build_autogen_chat_client`, sqlite memory store under `PYTHONPATH=src`.

**Spec:** `docs/superpowers/specs/2026-09-05-stage1-elicitation-loop-design.md` (amends `2026-09-04-stage1-elicitation-design.md`).

## Global Constraints

- **No keyword matching, string matching, or regex against user content. Ever.** Every judgement about meaning is a model call; comparisons are only over identifiers this system minted (cell ids, uids, row keys).
- **Independent model calls run concurrently.** The classify batch is one `asyncio.gather` under a semaphore; generate for the top three is one `gather`.
- **The read path never calls a model.** Nothing here touches `get_active_constraints`.
- **A sampling failure stays loud.** Non-schema content raises; a missing model client is `AdaptiveDependencyUnavailable`. No fallback marks a cell covered.
- **Never assert an exact model output string in a unit test.** Stub the client; assert the decision it drove.
- **Evals sample n=5 and assert on rates.** Never a single draw. No `temperature` pin.
- **The gate opens iff no cell is `uncovered`.** An ungroundable cell is recorded in `matrix.unaskable` and its state stays `uncovered`.
- **`PlanningContext.probes` never reaches the snapshot.** The surfaces build from the snapshot alone.
- **Work happens in `.claude/worktrees/stage1-elicitation` on `feat/stage1-elicitation-loop`.** Run everything with `PYTHONPATH=src` and the parent's venv: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python`. There is no `.env` in the worktree; the eval and relink steps say how to load the parent's.
- **The live store `data/memory.db` is Hugo's real corpus.** Every write to it is preceded by a dated `cp` backup. Never commit any copy of it.
- **Commit after every task.** Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

Shell alias used throughout (set once per shell):

```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.claude/worktrees/stage1-elicitation
export PYTHONPATH=src
PY=/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python
```

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `tests/unit/test_planning_reminder_suppression.py`, `tests/e2e/test_slack_handoff_flow.py` | the two stale tests | 1 |
| `src/fateforger/agents/timeboxing/elicitation.py` | `method` concern; `rule_placement`, `placed_against` on `CoverageMatrix` | 2, 3 |
| `src/fateforger/agents/timeboxing/session_contracts.py` | `ProbeDraft` | 4 |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | `PlanningContext.probes`; `_stage1_outcome` prefers the resolved probe | 4, 10 |
| `src/fateforger/agents/timeboxing/elicitation_judges.py` (new) | `PlacementJudge`, `CoverageJudge`, `ProbeJudge`, `Judges`, `build_judges`, `elicit` | 5–8 |
| `src/fateforger/slack_bot/timeboxing_host.py` | `_frame_from_corpus` runs `elicit` | 9 |
| `src/fateforger/agents/timeboxing/readiness.py`, `src/fateforger/slack_bot/stage_cards.py` | #294: `stage_of` on the instance; `map_outcome(requirements=)` | 11 |
| `tests/unit/test_elicitation_judges.py` (new) | judge and orchestrator plumbing, stubbed | 5–8 |
| `tests/unit/test_elicitation_composes.py` (new) | the three stub swaps and the card-side seam | 12 |
| `scripts/memory/relink_anchors.py` (new) | #290 dry-run / apply | 13 |
| `tests/fixtures/stage1/days.py`, `golden.toml` (new), `labels.toml` (deleted) | hash pin; golden days | 14, 15 |
| `tests/evals/test_stage1_fixture.py`, `tests/evals/test_stage1_elicitation.py` (new) | fixture shape; simulated user, measures, ablation | 14, 16 |
| `docs/superpowers/research/2026-09-0X-stage1-loop-evals.md` (new) | the eval numbers | 17 |

---

### Task 1: The two stale tests

**Files:**
- Modify: `tests/unit/test_planning_reminder_suppression.py:154-162`
- Modify: `tests/e2e/test_slack_handoff_flow.py:82-84`

**Interfaces:** none.

- [ ] **Step 1: Reproduce both failures**

Run: `$PY -m pytest tests/unit/test_planning_reminder_suppression.py tests/e2e/test_slack_handoff_flow.py -q -p no:randomly`
Expected: on a Saturday or Sunday, 2 FAIL with `calendar day_type must match the weekday-derived classification`; on any day, 1 FAIL at `test_slack_handoff_flow.py:82` with `assert False`. (On a weekday the two suppression tests pass; the fix still applies.)

- [ ] **Step 2: Derive the day type from the weekday in `_session`**

In `tests/unit/test_planning_reminder_suppression.py`, inside `_session`, replace

```python
                    day_type=DayType.WORKING,
```

with

```python
                    # `PlanningDay` refuses a calendar-basis day type that
                    # disagrees with the weekday; these tests build days
                    # relative to today, which is sometimes a weekend.
                    day_type=DayType.WEEKEND if day.isoweekday() in (6, 7) else DayType.WORKING,
```

- [ ] **Step 3: Assert the current post format in the e2e test**

#281 removed the `*agent*\ntext` wrapper (`_with_agent_attribution` now returns `to_mrkdwn(text)` for a text-only payload). In `tests/e2e/test_slack_handoff_flow.py` replace

```python
    assert any(
        p.get("text") == "*planner_agent*\nPlanner response" for p in client.posted
    )
```

with

```python
    assert any("Planner response" in (p.get("text") or "") for p in client.posted)
    assert not any("*planner_agent*\n" in (p.get("text") or "") for p in client.posted)
```

- [ ] **Step 4: Run both files**

Run: `$PY -m pytest tests/unit/test_planning_reminder_suppression.py tests/e2e/test_slack_handoff_flow.py -q -p no:randomly`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_planning_reminder_suppression.py tests/e2e/test_slack_handoff_flow.py
git commit -m "test: the reminder-suppression days follow the weekday; the handoff e2e asserts the post format #281 left

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The seventh concern

**Files:**
- Modify: `src/fateforger/agents/timeboxing/elicitation.py:50-60`
- Modify: `src/fateforger/agents/timeboxing/readiness.py:279-281` (docstring)
- Modify: `tests/unit/test_elicitation_gate.py`

**Interfaces:**
- Produces: `ROWS["method"]`, cell ids `elicit.method.{criterion}`; `len(ALL_CELLS) == 45`.

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_elicitation_gate.py`, replace `test_the_floor_has_eight_rows_and_forty_cells` with:

```python
def test_the_floor_has_nine_rows_and_forty_five_cells() -> None:
    assert len(ROWS) == 9
    assert len(CRITERIA) == 5
    assert len(ALL_CELLS) == 45
    assert "unplaced" in ROWS and "request" in ROWS
    assert ROWS["method"].label == "how the day gets planned"
    assert [c.key for c in CONCERNS][-1] == "method"
```

Add `CONCERNS` to the import from `fateforger.agents.timeboxing.elicitation`.

- [ ] **Step 2: Run it to verify it fails**

Run: `$PY -m pytest tests/unit/test_elicitation_gate.py::test_the_floor_has_nine_rows_and_forty_five_cells -q`
Expected: FAIL — `assert 8 == 9`.

- [ ] **Step 3: Add the concern**

In `src/fateforger/agents/timeboxing/elicitation.py`, replace the `CONCERNS` tuple with:

```python
#: Layer 1. Six concerns drafted from the anchor clusters, and a seventh Hugo
#: added on 2026-09-05: the rules about the planning itself (block exit
#: criteria, scheduling gates, duration caps) fit no concern about a thing in
#: the day and carry no anchor, so placement routes them here by rule name.
CONCERNS: tuple[Concern, ...] = (
    Concern("bounded", "how the day is bounded", "when it starts and ends, what frames it"),
    Concern("fixed", "what is fixed", "events, appointments, arrivals that do not move"),
    Concern("movement", "movement and transitions", "commutes, travel, the gaps between fixed things"),
    Concern("body", "body", "food, sleep, energy, exercise; the physical constraints on attention"),
    Concern("fragile", "fragile intentions", "the things that only happen if protected"),
    Concern("not_today", "what today is not", "rules that usually hold and do not today"),
    Concern("method", "how the day gets planned", "rules about the planning itself: gates, caps, orderings; not about a thing in the day"),
)
```

In `src/fateforger/agents/timeboxing/readiness.py`, change the `_cell_requirements` docstring's first line from `"""Forty requirements from two fixed lists.` to `"""Forty-five requirements from two fixed lists.`.

- [ ] **Step 4: Find every other assertion of forty or eight rows**

Run: `grep -rn "== 40\|== 8\b\|forty\|Forty" tests/unit/test_elicitation_gate.py tests/unit/test_stage1_fixture_shapes.py tests/unit/test_adaptive_stage1.py tests/unit/test_stage_context.py src/fateforger/agents/timeboxing/elicitation.py`
Expected hits: the `CoverageMatrix` validator docstring in `elicitation.py` ("exactly the forty ALL_CELLS") — change "forty" to "every"; any test asserting `40` — change to `45`. Nothing else is hardcoded (`admonish-1-52` checked the card grammar on 2026-09-05).

- [ ] **Step 5: Run the unit suite for the timeboxing package**

Run: `$PY -m pytest tests/unit -q -m "not slow" -p no:randomly -k "elicit or stage1 or stage_context or stage_cards or adaptive or readiness"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/elicitation.py src/fateforger/agents/timeboxing/readiness.py tests/unit
git commit -m "feat(timeboxing): the seventh concern: how the day gets planned; forty-five cells

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The matrix remembers what it was placed against

**Files:**
- Modify: `src/fateforger/agents/timeboxing/elicitation.py` (`CoverageMatrix`)
- Test: `tests/unit/test_elicitation_gate.py`

**Interfaces:**
- Produces: `CoverageMatrix.rule_placement: dict[str, str]`, `CoverageMatrix.placed_against: list[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_elicitation_gate.py`:

```python
def test_the_matrix_carries_rule_placement_and_what_it_was_placed_against() -> None:
    matrix = _matrix()
    matrix = matrix.model_copy(
        update={
            "placement": {"a-gym": "body"},
            "rule_placement": {"c-exit": "method"},
            "placed_against": ["a-gym", "c-exit"],
        }
    )
    again = CoverageMatrix.model_validate(matrix.model_dump(mode="json"))
    assert again.rule_placement == {"c-exit": "method"}
    assert again.placed_against == ["a-gym", "c-exit"]


def test_a_matrix_without_the_new_fields_still_validates() -> None:
    """A matrix written before this build has neither field; it must still parse."""
    value = _matrix().model_dump(mode="json")
    value.pop("rule_placement")
    value.pop("placed_against")
    assert CoverageMatrix.model_validate(value).placed_against == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/unit/test_elicitation_gate.py -q -k "rule_placement or new_fields"`
Expected: FAIL — `extra_forbidden` on `rule_placement`.

- [ ] **Step 3: Add the fields**

In `CoverageMatrix`, after `placement`:

```python
    #: unanchored rule uid -> row key; the rules placement put under a concern
    #: by name because no anchor could carry them there
    rule_placement: dict[str, str] = Field(default_factory=dict)
    #: the sorted anchor and rule uids the placement was made against; the
    #: orchestrator reuses the placement iff the day's set is the same
    placed_against: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run the file**

Run: `$PY -m pytest tests/unit/test_elicitation_gate.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/elicitation.py tests/unit/test_elicitation_gate.py
git commit -m "feat(timeboxing): the matrix records rule placement and the uid set it was placed against

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `ProbeDraft` and `PlanningContext.probes`

**Files:**
- Modify: `src/fateforger/agents/timeboxing/session_contracts.py` (after `Gate`)
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py:82-100` (`PlanningContext`)
- Test: `tests/unit/test_adaptive_stage1.py`

**Interfaces:**
- Produces: `ProbeDraft(cell_id: str, question: str, why_needed: str, options: list[BlockerOption] ≤ 4)`; `PlanningContext.probes: list[ProbeDraft]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_adaptive_stage1.py`:

```python
def test_a_planning_context_carries_probe_drafts_and_they_never_reach_the_snapshot() -> None:
    from fateforger.agents.timeboxing.session_contracts import ProbeDraft

    probe = ProbeDraft(cell_id="elicit.body.unclear", question="How long is the gym?", why_needed="body")
    context = PlanningContext(probes=[probe])
    assert context.probes[0].cell_id == "elicit.body.unclear"
    assert "probes" not in PlanningSessionSnapshot.model_fields
```

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/unit/test_adaptive_stage1.py -q -k probe_drafts`
Expected: FAIL — `ImportError: cannot import name 'ProbeDraft'`.

- [ ] **Step 3: Add the contract and the field**

In `session_contracts.py`, after the `Gate` class:

```python
class ProbeDraft(_StrictModel):
    """One question a judge phrased for one open cell, resolved by the host.

    Carried on `PlanningContext`, never on the snapshot: the surfaces build
    from the snapshot alone, and a probe is one turn's phrasing of a cell the
    matrix already holds. `options` is non-empty only when the answer set is
    closed; every option id is minted by the host from the cell id.
    """

    cell_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    why_needed: str = Field(min_length=1)
    options: list[BlockerOption] = Field(default_factory=list, max_length=4)
```

In `adaptive_timeboxing.py`, add `ProbeDraft` to the `from .session_contracts import (...)` list and, in `PlanningContext` after `calendar_snapshot`:

```python
    #: Probes the host's judges phrased for the top open Stage 1 cells, in
    #: rank order. Read by `_stage1_outcome` and nowhere else.
    probes: list[ProbeDraft] = Field(default_factory=list)
```

- [ ] **Step 4: Run the file**

Run: `$PY -m pytest tests/unit/test_adaptive_stage1.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/session_contracts.py src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/unit/test_adaptive_stage1.py
git commit -m "feat(timeboxing): ProbeDraft, carried on the planning context and never the snapshot

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `PlacementJudge`

**Files:**
- Create: `src/fateforger/agents/timeboxing/elicitation_judges.py`
- Create: `tests/unit/test_elicitation_judges.py`

**Interfaces:**
- Produces: `Placement(anchors: dict[str, str], rules: dict[str, str])`; `PlacementJudge(model_client).place(*, anchors: list[dict], unanchored_rules: list[dict], session_key: str) -> Placement`; `PLACEMENT_TARGETS`; `anchors_in(rows) -> list[dict]`; `unanchored_in(rows) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_elicitation_judges.py`:

```python
"""The three Stage 1 judges and the orchestrator, with the model stubbed.

Every assertion is on what was sent and what the answer became -- never on
the model's words. The judges follow `DayFrameJudge`; the stub client is the
one `tests/unit/test_day_frame_on_record.py` uses.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CONCERNS, ROWS, CoverageMatrix
from fateforger.agents.timeboxing.elicitation_judges import (
    PLACEMENT_TARGETS,
    PlacementJudge,
    anchors_in,
    unanchored_in,
)
from fateforger.agents.timeboxing.session_contracts import (
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)

DAY = date(2026, 9, 8)


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


GYM = {"uid": "a-gym", "name": "gym"}
DINNER = {"uid": "a-din", "name": "dinner"}
ROWS_FIXTURE = [
    {"uid": "c-oats", "name": "Oats before gym", "description": "Eat oats two hours before the gym.", "necessity": "must", "anchors": [GYM]},
    {"uid": "c-run", "name": "Run at 18:00", "description": "Run at 18:00 when cooking dinner.", "necessity": "should", "anchors": [GYM, DINNER]},
    {"uid": "c-exit", "name": "Block exit criteria", "description": "Every block ends with a written exit criterion.", "necessity": "must", "anchors": []},
]


def test_placement_targets_are_the_concerns_plus_unplaced() -> None:
    assert PLACEMENT_TARGETS == (*(c.key for c in CONCERNS), "unplaced")
    assert "request" not in PLACEMENT_TARGETS


def test_anchors_in_groups_rows_by_anchor_with_two_example_names() -> None:
    anchors = anchors_in(ROWS_FIXTURE)
    by_uid = {a["uid"]: a for a in anchors}
    assert set(by_uid) == {"a-gym", "a-din"}
    assert by_uid["a-gym"]["name"] == "gym"
    assert by_uid["a-gym"]["example_rules"] == ["Oats before gym", "Run at 18:00"]
    assert by_uid["a-din"]["example_rules"] == ["Run at 18:00"]


def test_unanchored_in_returns_the_rules_with_no_anchor() -> None:
    assert [r["uid"] for r in unanchored_in(ROWS_FIXTURE)] == ["c-exit"]
    assert unanchored_in(ROWS_FIXTURE)[0]["description"].startswith("Every block")


@pytest.mark.asyncio
async def test_placement_maps_every_offered_uid_to_a_row() -> None:
    client = _SchemaOutputClient(
        {
            "anchors": [{"uid": "a-gym", "row": "body"}, {"uid": "a-din", "row": "fixed"}],
            "rules": [{"uid": "c-exit", "row": "method"}],
        }
    )
    placement = await PlacementJudge(client).place(
        anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
    )
    assert placement.anchors == {"a-gym": "body", "a-din": "fixed"}
    assert placement.rules == {"c-exit": "method"}
    sent = json.loads(client.calls[0][0][1].content)
    assert [a["uid"] for a in sent["anchors"]] == ["a-gym", "a-din"]
    assert [r["uid"] for r in sent["rules"]] == ["c-exit"]
    assert [c["key"] for c in sent["rows"]] == list(PLACEMENT_TARGETS)


@pytest.mark.asyncio
async def test_placement_refuses_a_uid_it_did_not_offer() -> None:
    client = _SchemaOutputClient(
        {"anchors": [{"uid": "a-gym", "row": "body"}, {"uid": "a-din", "row": "fixed"}, {"uid": "a-ghost", "row": "body"}], "rules": [{"uid": "c-exit", "row": "method"}]}
    )
    with pytest.raises(ValueError, match="a-ghost"):
        await PlacementJudge(client).place(
            anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_placement_refuses_to_leave_an_offered_uid_unplaced() -> None:
    client = _SchemaOutputClient({"anchors": [{"uid": "a-gym", "row": "body"}], "rules": []})
    with pytest.raises(ValueError, match="a-din"):
        await PlacementJudge(client).place(
            anchors=anchors_in(ROWS_FIXTURE), unanchored_rules=unanchored_in(ROWS_FIXTURE), session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_placement_with_nothing_to_place_makes_no_call() -> None:
    client = _SchemaOutputClient()
    placement = await PlacementJudge(client).place(anchors=[], unanchored_rules=[], session_key="C1:1.0")
    assert placement.anchors == {} and placement.rules == {}
    assert client.calls == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q`
Expected: FAIL — `ModuleNotFoundError: fateforger.agents.timeboxing.elicitation_judges`.

- [ ] **Step 3: Create the module with the placement judge**

Create `src/fateforger/agents/timeboxing/elicitation_judges.py`:

```python
"""The three judgements that fill the Stage 1 coverage matrix, and the loop.

Nothing in `elicitation.py` calls a model: it holds the floor and the
arithmetic gate. This module holds the three judgements the parent design
placed in the host's `resolve` -- place anchors under rows, classify each cell,
phrase a probe -- each on the `DayFrameJudge` pattern: a model client in, one
schema-bound call, raise on anything that is not the schema. `elicit` runs
them in the order the design's plan lists and returns one matrix fact and the
probes that grounded; the kernel stays arithmetic.

Design: docs/superpowers/specs/2026-09-05-stage1-elicitation-loop-design.md
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import BaseModel, ConfigDict, Field

from fateforger.core.llm_attribution import llm_attribution

from .elicitation import (
    ALL_CELLS,
    CONCERNS,
    CRITERION_BY_KEY,
    ROWS,
    CellState,
    Concern,
    CoverageMatrix,
    RowStats,
    coverage_matrix,
    ranked_open_cells,
)
from .session_contracts import (
    BlockerOption,
    CellRef,
    FactKind,
    PlanningFact,
    PlanningSessionSnapshot,
    ProbeDraft,
    coverage_fact_id,
)

#: Where placement may put an anchor or an unanchored rule. `request` is a
#: row but never a placement target: it holds what the user asked for.
PLACEMENT_TARGETS: tuple[str, ...] = (*(c.key for c in CONCERNS), "unplaced")
_PlacementTarget = Literal[PLACEMENT_TARGETS]  # type: ignore[valid-type]


def _rows_for_prompt() -> list[dict[str, str]]:
    return [
        {"key": key, "label": ROWS[key].label, "description": ROWS[key].description}
        for key in PLACEMENT_TARGETS
    ]


def anchors_in(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every anchor the rows carry, with up to two rule names as context.

    Grouping by anchor uid and taking names in row order: arithmetic over
    identifiers the memory server minted.
    """
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        for anchor in row.get("anchors") or []:
            uid = str(anchor["uid"])
            entry = seen.setdefault(uid, {"uid": uid, "name": str(anchor["name"]), "example_rules": []})
            if len(entry["example_rules"]) < 2:
                entry["example_rules"].append(str(row["name"]))
    return list(seen.values())


def unanchored_in(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rules no anchor carries, by name and description."""
    return [
        {"uid": str(row["uid"]), "name": str(row["name"]), "description": str(row.get("description") or "")}
        for row in rows
        if not (row.get("anchors") or [])
    ]


class _Placed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    uid: str
    row: _PlacementTarget


class _PlacementJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    anchors: list[_Placed]
    rules: list[_Placed]


class Placement(BaseModel):
    """anchor uid -> row key, and unanchored rule uid -> row key."""

    model_config = ConfigDict(extra="forbid")

    anchors: dict[str, str] = Field(default_factory=dict)
    rules: dict[str, str] = Field(default_factory=dict)


_PLACEMENT_PROMPT = """You are typing categories for a personal day-planner.
Each ANCHOR is a thing the user has stated rules about; each RULE under
"rules" is a rule no anchor carries. Place every anchor and every rule under
exactly one ROW by its key, or under "unplaced" when no row fits. Decide
from what the anchor or rule is, using the example rule names only as
context. A rule about how the day is planned -- a gate, a cap, an ordering --
belongs under "method", not under the thing it mentions. Echo every uid you
were given exactly once and never invent one. Return only the requested
schema.
"""


class PlacementJudge:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def place(
        self,
        *,
        anchors: list[dict[str, Any]],
        unanchored_rules: list[dict[str, Any]],
        session_key: str,
    ) -> Placement:
        offered_anchors = {str(a["uid"]) for a in anchors}
        offered_rules = {str(r["uid"]) for r in unanchored_rules}
        if not offered_anchors and not offered_rules:
            return Placement()
        prompt = json.dumps(
            {"rows": _rows_for_prompt(), "anchors": anchors, "rules": unanchored_rules},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        with llm_attribution(agent="timeboxing_agent", call_label="stage1_placement", key=session_key):
            result = await self.model_client.create(
                [SystemMessage(content=_PLACEMENT_PROMPT), UserMessage(content=prompt, source="user")],
                json_output=_PlacementJudgement,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError("placement judgement returned no schema-bound JSON content")
        judgement = _PlacementJudgement.model_validate_json(content)
        placed_anchors = {p.uid: p.row for p in judgement.anchors}
        placed_rules = {p.uid: p.row for p in judgement.rules}
        # Set arithmetic over uids this system minted: nothing invented, nothing
        # dropped. An anchor left out would silently make its rules unreachable
        # by the ranking; an invented one would place nothing.
        for label, offered, placed in (("anchors", offered_anchors, placed_anchors), ("rules", offered_rules, placed_rules)):
            unknown = sorted(set(placed) - offered)
            if unknown:
                raise ValueError(f"placement named {label} it was not shown: {unknown}")
            missing = sorted(offered - set(placed))
            if missing:
                raise ValueError(f"placement left {label} unplaced: {missing}")
        return Placement(anchors=placed_anchors, rules=placed_rules)


__all__ = ["PLACEMENT_TARGETS", "Placement", "PlacementJudge", "anchors_in", "unanchored_in"]
```

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/elicitation_judges.py tests/unit/test_elicitation_judges.py
git commit -m "feat(timeboxing): PlacementJudge: anchors and unanchored rules under the floor's rows, one batched call

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `CoverageJudge`

**Files:**
- Modify: `src/fateforger/agents/timeboxing/elicitation_judges.py`
- Modify: `tests/unit/test_elicitation_judges.py`

**Interfaces:**
- Produces: `CoverageJudge(model_client).classify(*, cell: CellRef, rules: list[dict], stated: list[str], request: str | None, session_key: str) -> tuple[CellState, str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_elicitation_judges.py` (add `CoverageJudge` to the import):

```python
@pytest.mark.asyncio
async def test_classify_sends_the_row_the_criterion_and_names_only() -> None:
    client = _SchemaOutputClient({"status": "uncovered", "why": "no duration"})
    cell = CellRef(row="body", criterion="tacit_knowledge")
    state, why = await CoverageJudge(client).classify(
        cell=cell,
        rules=[{"name": "Oats before gym", "necessity": "must", "description": "SHOULD NOT BE SENT"}],
        stated=["gym at 18:00"],
        request="deep work in the morning, gym at 18:00",
        session_key="C1:1.0",
    )
    assert state == "uncovered"
    assert why == "no duration"
    sent = json.loads(client.calls[0][0][1].content)
    assert sent["row"]["key"] == "body"
    assert sent["criterion"]["key"] == "tacit_knowledge"
    assert sent["rules"] == [{"name": "Oats before gym", "necessity": "must"}]
    assert sent["stated"] == ["gym at 18:00"]
    assert sent["request"] == "deep work in the morning, gym at 18:00"


@pytest.mark.asyncio
async def test_classify_refuses_a_status_outside_the_schema() -> None:
    client = _SchemaOutputClient({"status": "maybe", "why": ""})
    with pytest.raises(ValueError):
        await CoverageJudge(client).classify(
            cell=CellRef(row="body", criterion="unclear"), rules=[], stated=[], request=None, session_key="C1:1.0"
        )
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q -k classify`
Expected: FAIL — `ImportError: cannot import name 'CoverageJudge'`.

- [ ] **Step 3: Add the judge**

Append to `elicitation_judges.py` before `__all__` (and add `"CoverageJudge"` to `__all__`):

```python
class _CoverageJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: CellState
    #: Kept for the eval report; never rendered. No length cap: a wrong
    #: `status` would corrupt the gate, a long `why` corrupts nothing, and a
    #: cap here would fail a whole batched turn over cosmetic text (ruled
    #: 2026-09-05, Task 6 review).
    why: str


_COVERAGE_PROMPT = """You audit an elicitation conversation for a personal day
planner, before the day is planned. Decide, for ONE criterion about ONE row of
concern, whether the conversation so far settles it. Base the decision only on
the rules on record for this row and on what the user said this session; do
not invent concerns never raised.

status "covered": settled for this day. status "uncovered": a good coach would
ask about this before planning. status "not_applicable": there is nothing in
this row to have this criterion about. For the "alternatives" criterion,
answer "uncovered" only where a rule in this row is at risk given what the
user said today; a contingency nobody needs is not a gap. Give "why" in at
most fifteen words. Return only the requested schema.
"""


class CoverageJudge:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def classify(
        self,
        *,
        cell: CellRef,
        rules: list[dict[str, Any]],
        stated: list[str],
        request: str | None,
        session_key: str,
    ) -> tuple[CellState, str]:
        row: Concern = ROWS[cell.row]
        criterion = CRITERION_BY_KEY[cell.criterion]
        prompt = json.dumps(
            {
                "row": {"key": row.key, "label": row.label, "description": row.description},
                "criterion": {"key": criterion.key, "question": criterion.question},
                # Names and necessity only: full descriptions go to the one
                # generate call, which halves the tokens of the batch.
                "rules": [{"name": str(r["name"]), "necessity": str(r["necessity"])} for r in rules],
                "stated": stated,
                "request": request,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        with llm_attribution(agent="timeboxing_agent", call_label=f"stage1_classify:{cell.id}", key=session_key):
            result = await self.model_client.create(
                [SystemMessage(content=_COVERAGE_PROMPT), UserMessage(content=prompt, source="user")],
                json_output=_CoverageJudgement,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError(f"coverage judgement for {cell.id} returned no schema-bound JSON content")
        judgement = _CoverageJudgement.model_validate_json(content)
        return judgement.status, judgement.why
```

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/elicitation_judges.py tests/unit/test_elicitation_judges.py
git commit -m "feat(timeboxing): CoverageJudge: one narrow call per cell, names and necessity only

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `ProbeJudge`

**Files:**
- Modify: `src/fateforger/agents/timeboxing/elicitation_judges.py`
- Modify: `tests/unit/test_elicitation_judges.py`

**Interfaces:**
- Produces: `ProbeJudge(model_client).generate(*, cell: CellRef, rules_full: list[dict], conversation: list[str], request: str | None, session_key: str) -> ProbeDraft | None`.

- [ ] **Step 1: Write the failing tests**

Append (add `ProbeJudge` to the import):

```python
@pytest.mark.asyncio
async def test_generate_returns_a_draft_with_host_minted_option_ids() -> None:
    client = _SchemaOutputClient(
        {"grounded": True, "question": "How long is the gym?", "why_needed": "to place it", "options": ["60 min", "90 min"]}
    )
    cell = CellRef(row="body", criterion="tacit_knowledge")
    draft = await ProbeJudge(client).generate(
        cell=cell,
        rules_full=[{"name": "Oats before gym", "necessity": "must", "description": "Eat oats two hours before the gym."}],
        conversation=["deep work in the morning, gym at 18:00"],
        request="deep work in the morning, gym at 18:00",
        session_key="C1:1.0",
    )
    assert draft is not None
    assert draft.cell_id == cell.id
    assert draft.question == "How long is the gym?"
    assert draft.why_needed == "to place it"
    assert [o.option_id for o in draft.options] == ["elicit.body.tacit_knowledge:1", "elicit.body.tacit_knowledge:2"]
    assert [o.label for o in draft.options] == ["60 min", "90 min"]
    sent = json.loads(client.calls[0][0][1].content)
    assert sent["rules"][0]["description"].startswith("Eat oats")


@pytest.mark.asyncio
async def test_generate_may_return_nothing() -> None:
    client = _SchemaOutputClient({"grounded": False, "question": None, "why_needed": None, "options": []})
    draft = await ProbeJudge(client).generate(
        cell=CellRef(row="movement", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
    )
    assert draft is None


@pytest.mark.asyncio
async def test_generate_refuses_grounded_without_a_question() -> None:
    client = _SchemaOutputClient({"grounded": True, "question": None, "why_needed": None, "options": []})
    with pytest.raises(ValueError, match="grounded"):
        await ProbeJudge(client).generate(
            cell=CellRef(row="movement", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_generate_refuses_more_than_four_options() -> None:
    client = _SchemaOutputClient({"grounded": True, "question": "Which?", "why_needed": "w", "options": ["a", "b", "c", "d", "e"]})
    with pytest.raises(ValueError, match="at most four"):
        await ProbeJudge(client).generate(
            cell=CellRef(row="body", criterion="unclear"), rules_full=[], conversation=[], request=None, session_key="C1:1.0"
        )
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q -k generate`
Expected: FAIL — `ImportError: cannot import name 'ProbeJudge'`.

- [ ] **Step 3: Add the judge**

Append before `__all__` (add `"ProbeJudge"`):

```python
class _ProbeJudgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: False means nothing the user said grounds a question about this cell.
    grounded: bool
    question: str | None
    why_needed: str | None
    #: Offered only when the answer set is closed. At most four -- enforced
    #: after parsing, not as a schema keyword: `maxItems` falls outside the
    #: strict structured-output subset the `:nitro` hosts enforce (Task 7
    #: review, 2026-09-05). Slack renders at most four buttons and
    #: `ProbeDraft.options` caps at the same number.
    options: list[str] = Field(default_factory=list)


_PROBE_PROMPT = """You are a coach helping someone plan one day, asking one
follow-up question before planning starts. You are given one open concern
(the row), one criterion it fails, the rules on record for that row with
their full descriptions, and everything the user has said this session.

Write one question, based only on what the user has said and what is on
record. It must be: specific to this person and this day, not generic; short;
plain words, no jargon and nothing technical; appropriate to the person; a
question about what holds, never a request for a solution; about one kind of
thing at a time; open to only one reading; and concrete enough to be
answerable. Give "why_needed" as a few words on what the answer lets the
planner place. Offer "options" only when the sensible answers form a closed
set of at most four; otherwise leave it empty.

If nothing the user has said grounds a question about this cell, set grounded
to false and leave the rest null: a no-op is a perfectly good outcome; do not
invent a question to justify the run. Return only the requested schema.
"""


class ProbeJudge:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def generate(
        self,
        *,
        cell: CellRef,
        rules_full: list[dict[str, Any]],
        conversation: list[str],
        request: str | None,
        session_key: str,
    ) -> ProbeDraft | None:
        row: Concern = ROWS[cell.row]
        criterion = CRITERION_BY_KEY[cell.criterion]
        prompt = json.dumps(
            {
                "row": {"key": row.key, "label": row.label, "description": row.description},
                "criterion": {"key": criterion.key, "question": criterion.question},
                "rules": [
                    {"name": str(r["name"]), "necessity": str(r["necessity"]), "description": str(r.get("description") or "")}
                    for r in rules_full
                ],
                "conversation": conversation,
                "request": request,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        with llm_attribution(agent="timeboxing_agent", call_label=f"stage1_probe:{cell.id}", key=session_key):
            result = await self.model_client.create(
                [SystemMessage(content=_PROBE_PROMPT), UserMessage(content=prompt, source="user")],
                json_output=_ProbeJudgement,
            )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError(f"probe judgement for {cell.id} returned no schema-bound JSON content")
        judgement = _ProbeJudgement.model_validate_json(content)
        if not judgement.grounded:
            return None
        if not judgement.question or not judgement.why_needed:
            raise ValueError(f"probe judgement for {cell.id} said grounded and gave no question or reason")
        if len(judgement.options) > 4:
            raise ValueError(f"probe judgement for {cell.id} offered {len(judgement.options)} options; at most four")
        return ProbeDraft(
            cell_id=cell.id,
            question=judgement.question,
            why_needed=judgement.why_needed,
            # Option ids are minted here from the cell id, never by the model.
            options=[
                BlockerOption(option_id=f"{cell.id}:{index}", label=label, effect=label)
                for index, label in enumerate(judgement.options, start=1)
            ],
        )
```

- [ ] **Step 4: Run the tests**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/elicitation_judges.py tests/unit/test_elicitation_judges.py
git commit -m "feat(timeboxing): ProbeJudge: one grounded question for a cell, or nothing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The orchestrator `elicit`

**Files:**
- Modify: `src/fateforger/agents/timeboxing/elicitation_judges.py`
- Modify: `tests/unit/test_elicitation_judges.py`

**Interfaces:**
- Produces: `Judges(placement, coverage, probe)`; `build_judges(model_client) -> Judges`; `ElicitationResult(matrix_fact: PlanningFact, probes: list[ProbeDraft])`; `elicit(snapshot, rows, judges, *, session_key, concurrency=16, generate_for=3) -> ElicitationResult`.
- Consumes: `coverage_matrix`, `ranked_open_cells`, `RowStats`, `CoverageMatrix` from `elicitation.py`; the three judges above.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_elicitation_judges.py` (add `Judges`, `elicit`, `ElicitationResult` to the import, plus `from fateforger.agents.timeboxing.session_contracts import CellRef, PlannerAssumption, coverage_fact_id, elicited_fact_id`):

```python
class _StubPlacement:
    def __init__(self, placement: dict[str, str], rules: dict[str, str] | None = None) -> None:
        self._placement = placement
        self._rules = rules or {}
        self.calls = 0

    async def place(self, *, anchors, unanchored_rules, session_key):  # noqa: ANN001
        from fateforger.agents.timeboxing.elicitation_judges import Placement

        self.calls += 1
        return Placement(anchors=self._placement, rules=self._rules)


class _StubCoverage:
    """`table` maps cell id -> state; anything else answers `covered`."""

    def __init__(self, table: dict[str, str]) -> None:
        self.table = table
        self.asked: list[str] = []

    async def classify(self, *, cell, rules, stated, request, session_key):  # noqa: ANN001
        self.asked.append(cell.id)
        return self.table.get(cell.id, "covered"), "stub"


class _StubProbe:
    """`grounded` is the set of cell ids that get a draft."""

    def __init__(self, grounded: set[str]) -> None:
        self.grounded = grounded
        self.asked: list[str] = []

    async def generate(self, *, cell, rules_full, conversation, request, session_key):  # noqa: ANN001
        from fateforger.agents.timeboxing.session_contracts import ProbeDraft

        self.asked.append(cell.id)
        if cell.id not in self.grounded:
            return None
        return ProbeDraft(cell_id=cell.id, question=f"about {cell.id}?", why_needed="stub")


def _snapshot(*facts: PlanningFact, assumptions: list[PlannerAssumption] | None = None) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=DAY, timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING),
        facts=[
            PlanningFact(fact_id="request-1", kind=FactKind.REQUESTED_ACTIVITY, value="deep work in the morning, gym at 18:00", source="user"),
            *facts,
        ],
        assumptions=list(assumptions or []),
    )


PLACED = {"a-gym": "body", "a-din": "fixed"}


def _judges(coverage: dict[str, str], grounded: set[str] | None = None, placement: dict[str, str] = PLACED):
    return Judges(
        placement=_StubPlacement(placement, {"c-exit": "method"}),
        coverage=_StubCoverage(coverage),
        probe=_StubProbe(grounded if grounded is not None else set()),
    )


def _run(snapshot, judges, rows=ROWS_FIXTURE) -> ElicitationResult:
    return asyncio.run(elicit(snapshot, rows, judges, session_key="C1:1.0"))


def test_rows_with_no_rules_and_nothing_stated_are_not_applicable_without_a_call() -> None:
    judges = _judges({})
    result = _run(_snapshot(), judges)
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    # movement, fragile, not_today, unplaced: no rules placed, nothing stated
    for row in ("movement", "fragile", "not_today", "unplaced"):
        for criterion in ("tacit_assumptions", "alternatives", "unclear", "contradictory", "tacit_knowledge"):
            assert matrix.cells[f"elicit.{row}.{criterion}"] == "not_applicable"
    assert not any(cell.startswith("elicit.movement.") for cell in judges.coverage.asked)
    # body (gym rules), fixed (dinner), method (exit criteria), request (stated): classified
    assert any(cell.startswith("elicit.body.") for cell in judges.coverage.asked)
    assert any(cell.startswith("elicit.method.") for cell in judges.coverage.asked)
    assert any(cell.startswith("elicit.request.") for cell in judges.coverage.asked)


def test_the_matrix_fact_is_written_whole_at_the_stable_id_with_placement() -> None:
    result = _run(_snapshot(), _judges({}))
    assert result.matrix_fact.fact_id == coverage_fact_id(DAY)
    assert result.matrix_fact.kind is FactKind.COVERAGE_MATRIX
    assert result.matrix_fact.source == "system"
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert set(matrix.cells) == {c.id for c in ALL_CELLS}
    assert matrix.placement == PLACED
    assert matrix.rule_placement == {"c-exit": "method"}
    assert matrix.placed_against == ["a-din", "a-gym", "c-exit"]
    assert matrix.rows["body"].rule_count == 2 and matrix.rows["body"].must_count == 1
    assert matrix.rows["method"].rule_count == 1
    assert matrix.rows["request"].stated == 1


def test_placement_is_reused_when_the_uid_set_is_unchanged_and_redone_when_it_moves() -> None:
    judges = _judges({})
    first = _run(_snapshot(), judges)
    assert judges.placement.calls == 1
    again = _run(_snapshot(first.matrix_fact), judges)
    assert judges.placement.calls == 1
    matrix = CoverageMatrix.model_validate(again.matrix_fact.value)
    assert matrix.placement == PLACED
    fewer = [row for row in ROWS_FIXTURE if row["uid"] != "c-exit"]
    asyncio.run(elicit(_snapshot(first.matrix_fact), fewer, judges, session_key="C1:1.0"))
    assert judges.placement.calls == 2


def test_a_cell_already_covered_is_not_classified_again() -> None:
    judges = _judges({"elicit.body.unclear": "uncovered"})
    first = _run(_snapshot(), judges)
    asked_first = set(judges.coverage.asked)
    assert "elicit.body.tacit_knowledge" in asked_first
    judges.coverage.asked.clear()
    _run(_snapshot(first.matrix_fact), judges)
    assert "elicit.body.tacit_knowledge" not in judges.coverage.asked  # was covered
    assert "elicit.body.unclear" in judges.coverage.asked  # still open, re-asked


def test_probes_come_from_the_top_three_ranked_cells_and_ungroundable_ones_stay_uncovered() -> None:
    open_cells = {
        "elicit.body.unclear": "uncovered",
        "elicit.body.tacit_knowledge": "uncovered",
        "elicit.fixed.unclear": "uncovered",
        "elicit.request.unclear": "uncovered",
    }
    judges = _judges(open_cells, grounded={"elicit.body.tacit_knowledge", "elicit.fixed.unclear"})
    result = _run(_snapshot(), judges)
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    # Rank as the orchestrator did before it learned which cells ground:
    # the final matrix already sorts the ungroundable cell last.
    ranked = ranked_open_cells(matrix.model_copy(update={"unaskable": []}))
    assert judges.probe.asked == [c.id for c in ranked[:3]]
    assert [p.cell_id for p in result.probes] == [c.id for c in ranked[:3] if c.id in judges.probe.grounded]
    ungrounded = [c.id for c in ranked[:3] if c.id not in judges.probe.grounded]
    assert ungrounded and set(ungrounded) <= set(matrix.unaskable)
    for cell in ungrounded:
        assert matrix.cells[cell] == "uncovered"


def test_a_cell_the_user_assumed_past_is_not_generated_for() -> None:
    assumed = PlannerAssumption(assumption_id="as-1", requirement_id="elicit.body.unclear", value="fine", why_needed="w", filed_by="user")
    judges = _judges({"elicit.body.unclear": "uncovered"}, grounded={"elicit.body.unclear"})
    result = _run(_snapshot(assumptions=[assumed]), judges)
    assert judges.probe.asked == []
    assert result.probes == []


def test_stated_facts_reach_the_classifier_and_the_generator() -> None:
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:30"}, source="user")
    said = PlanningFact(fact_id=elicited_fact_id("elicit.body.unclear"), kind=FactKind.ELICITED_STATEMENT, value={"cell": "elicit.body.unclear", "text": "gym is 75 minutes"}, source="user")

    class _Recording(_StubCoverage):
        def __init__(self) -> None:
            super().__init__({})
            self.stated: list[list[str]] = []

        async def classify(self, *, cell, rules, stated, request, session_key):  # noqa: ANN001
            self.stated.append(list(stated))
            return await super().classify(cell=cell, rules=rules, stated=stated, request=request, session_key=session_key)

    coverage = _Recording()
    judges = Judges(placement=_StubPlacement(PLACED, {"c-exit": "method"}), coverage=coverage, probe=_StubProbe(set()))
    result = _run(_snapshot(frame, said), judges)
    assert coverage.stated and all("gym is 75 minutes" in s for s in coverage.stated)
    assert all(any("07:00" in line for line in s) for s in coverage.stated)
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert matrix.rows["body"].stated == 1
    assert matrix.rows["bounded"].stated == 1


def test_a_suspended_rule_is_not_placed_or_counted() -> None:
    suspended = PlanningFact(fact_id="suspend:c-run", kind=FactKind.SUSPENDED_CONSTRAINT, value={"uid": "c-run", "reason": "not today"}, source="user")
    result = _run(_snapshot(suspended), _judges({}))
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert matrix.rows["body"].rule_count == 1
    assert "a-din" not in matrix.placed_against


def test_one_failing_classify_fails_the_turn_and_writes_nothing() -> None:
    class _Broken(_StubCoverage):
        async def classify(self, *, cell, rules, stated, request, session_key):  # noqa: ANN001
            if cell.id == "elicit.body.unclear":
                raise ValueError("model returned garbage")
            return "covered", "stub"

    judges = Judges(placement=_StubPlacement(PLACED, {"c-exit": "method"}), coverage=_Broken({}), probe=_StubProbe(set()))
    with pytest.raises(ValueError, match="garbage"):
        _run(_snapshot(), judges)


def test_elicit_needs_a_locked_day() -> None:
    bare = _snapshot().model_copy(update={"planning_day": None})
    with pytest.raises(ValueError, match="locked"):
        _run(bare, _judges({}))
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q -k "not_applicable or stable_id or reused or covered_is_not or top_three or assumed or stated_facts or suspended or failing or locked"`
Expected: FAIL — `ImportError: cannot import name 'Judges'`.

- [ ] **Step 3: Add the orchestrator**

Append before `__all__` (and add `"Judges"`, `"ElicitationResult"`, `"build_judges"`, `"elicit"` to `__all__`):

```python
@dataclass(frozen=True, slots=True)
class Judges:
    placement: PlacementJudge
    coverage: CoverageJudge
    probe: ProbeJudge


def build_judges(model_client: ChatCompletionClient) -> Judges:
    """The three judges on one client. The host imports this by name so a test
    can replace it with stubs without reaching into the host."""
    return Judges(
        placement=PlacementJudge(model_client),
        coverage=CoverageJudge(model_client),
        probe=ProbeJudge(model_client),
    )


@dataclass(frozen=True, slots=True)
class ElicitationResult:
    matrix_fact: PlanningFact
    probes: list[ProbeDraft]


def _suspended_uids(snapshot: PlanningSessionSnapshot) -> set[str]:
    found: set[str] = set()
    for fact in snapshot.facts:
        if fact.kind is not FactKind.SUSPENDED_CONSTRAINT:
            continue
        if not isinstance(fact.value, dict) or "uid" not in fact.value:
            raise ValueError(f"suspended-constraint fact {fact.fact_id!r} carries no uid")
        found.add(str(fact.value["uid"]))
    return found


def _request(snapshot: PlanningSessionSnapshot) -> str | None:
    for fact in snapshot.facts:
        if fact.kind is FactKind.REQUESTED_ACTIVITY and isinstance(fact.value, str):
            return fact.value
    return None


def _frame_line(snapshot: PlanningSessionSnapshot) -> str | None:
    for fact in snapshot.facts:
        if fact.kind is FactKind.DAY_FRAME and isinstance(fact.value, dict):
            wake = fact.value.get("wake")
            sleep = fact.value.get("sleep")
            return f"up at {wake or '?'}, asleep by {sleep or '?'}"
    return None


def _statements(snapshot: PlanningSessionSnapshot) -> list[tuple[str | None, str]]:
    """(cell id or None, text) for every elicited statement, in fact order."""
    out: list[tuple[str | None, str]] = []
    for fact in snapshot.facts:
        if fact.kind is FactKind.ELICITED_STATEMENT and isinstance(fact.value, dict):
            cell = fact.value.get("cell")
            out.append((str(cell) if isinstance(cell, str) else None, str(fact.value.get("text") or "")))
    return out


def _stated_lines(snapshot: PlanningSessionSnapshot) -> list[str]:
    lines = [text for _, text in _statements(snapshot)]
    frame = _frame_line(snapshot)
    return ([frame] if frame else []) + lines


def _row_of_statement(cell_id: str | None) -> str | None:
    """The row key a statement was filed against, from its cell id: a string
    this system minted as `elicit.{row}.{criterion}`."""
    if cell_id is None:
        return None
    for cell in ALL_CELLS:
        if cell.id == cell_id:
            return cell.row
    return None


def _row_stats(
    placement: Placement, rows: list[dict[str, Any]], snapshot: PlanningSessionSnapshot
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, RowStats]]:
    """Rules per row and the counts ranking reads; every term a count over
    minted fields. A rule with anchors under several rows lands in each."""
    by_row: dict[str, list[dict[str, Any]]] = {key: [] for key in ROWS}
    for row in rows:
        targets: set[str] = set()
        for anchor in row.get("anchors") or []:
            target = placement.anchors.get(str(anchor["uid"]))
            if target is not None:
                targets.add(target)
        if not (row.get("anchors") or []):
            targets.add(placement.rules.get(str(row["uid"]), "unplaced"))
        for target in targets:
            by_row[target].append(row)
    stated: dict[str, int] = {key: 0 for key in ROWS}
    for cell_id, _ in _statements(snapshot):
        row_key = _row_of_statement(cell_id)
        if row_key is not None:
            stated[row_key] += 1
    if _frame_line(snapshot) is not None:
        stated["bounded"] += 1
    if _request(snapshot) is not None:
        stated["request"] += 1
    stats = {
        key: RowStats(
            rule_count=len(by_row[key]),
            must_count=sum(1 for r in by_row[key] if str(r.get("necessity")) == "must"),
            stated=stated[key],
        )
        for key in ROWS
    }
    return by_row, stats


async def elicit(
    snapshot: PlanningSessionSnapshot,
    rows: list[dict[str, Any]],
    judges: Judges,
    *,
    session_key: str,
    concurrency: int = 16,
    generate_for: int = 3,
) -> ElicitationResult:
    """One iteration of the Stage 1 loop: place, classify, rank, generate.

    Everything fallible completes before anything is assembled; a turn is
    atomic. The matrix is rewritten whole at the day's stable id. Cells whose
    probe could not be grounded are recorded in `unaskable` and stay
    `uncovered`: the gate is "nothing uncovered", and `unaskable` only sorts
    a cell last.
    """
    if snapshot.planning_day is None:
        raise ValueError("elicit needs a locked planning day")
    day = snapshot.planning_day.date
    suspended = _suspended_uids(snapshot)
    live_rows = [row for row in rows if str(row.get("uid")) not in suspended]

    # 1. Place, reusing the cached placement iff the uid set is unchanged.
    anchors = anchors_in(live_rows)
    unanchored = unanchored_in(live_rows)
    against = sorted({a["uid"] for a in anchors} | {r["uid"] for r in unanchored})
    previous = coverage_matrix(snapshot)
    if previous is not None and previous.placed_against == against:
        placement = Placement(anchors=dict(previous.placement), rules=dict(previous.rule_placement))
    else:
        placement = await judges.placement.place(anchors=anchors, unanchored_rules=unanchored, session_key=session_key)

    # 2. Applicability, arithmetic.
    by_row, stats = _row_stats(placement, live_rows, snapshot)
    request = _request(snapshot)
    stated_lines = _stated_lines(snapshot)
    cells: dict[str, CellState] = {}
    to_classify: list[CellRef] = []
    for cell in ALL_CELLS:
        if previous is not None and previous.cells.get(cell.id) == "covered":
            cells[cell.id] = "covered"
            continue
        row_stats = stats[cell.row]
        if row_stats.rule_count == 0 and row_stats.stated == 0:
            cells[cell.id] = "not_applicable"
            continue
        to_classify.append(cell)

    # 3. Classify: one bounded-concurrency batch; any failure propagates.
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(cell: CellRef) -> tuple[str, CellState]:
        async with semaphore:
            state, _why = await judges.coverage.classify(
                cell=cell,
                rules=by_row[cell.row],
                stated=stated_lines,
                request=request,
                session_key=session_key,
            )
            return cell.id, state

    for cell_id, state in await asyncio.gather(*(_one(cell) for cell in to_classify)):
        cells[cell_id] = state

    still_open = {cell_id for cell_id, state in cells.items() if state == "uncovered"}
    unaskable = [cell_id for cell_id in (previous.unaskable if previous else []) if cell_id in still_open]
    matrix = CoverageMatrix(
        cells=cells,
        placement=placement.anchors,
        rule_placement=placement.rules,
        placed_against=against,
        rows=stats,
        unaskable=unaskable,
    )

    # 4. Rank.
    assumed = frozenset(a.requirement_id for a in snapshot.assumptions)
    ranked = ranked_open_cells(matrix, assumed)

    # 5. Generate for the top cells, in parallel.
    conversation = ([request] if request else []) + stated_lines
    targets = ranked[:generate_for]
    drafts = await asyncio.gather(
        *(
            judges.probe.generate(
                cell=cell, rules_full=by_row[cell.row], conversation=conversation, request=request, session_key=session_key
            )
            for cell in targets
        )
    )
    probes: list[ProbeDraft] = []
    for cell, draft in zip(targets, drafts, strict=True):
        if draft is None:
            if cell.id not in unaskable:
                unaskable.append(cell.id)
        else:
            if cell.id in unaskable:
                unaskable.remove(cell.id)
            probes.append(draft)
    matrix = matrix.model_copy(update={"unaskable": unaskable})

    # 6. Return; the fact is rewritten whole.
    fact = PlanningFact(
        fact_id=coverage_fact_id(day),
        kind=FactKind.COVERAGE_MATRIX,
        value=matrix.model_dump(mode="json"),
        source="system",
    )
    return ElicitationResult(matrix_fact=fact, probes=probes)
```

- [ ] **Step 4: Run the file**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the timeboxing unit tests**

Run: `$PY -m pytest tests/unit -q -m "not slow" -p no:randomly -k "elicit or stage1 or adaptive"`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/elicitation_judges.py tests/unit/test_elicitation_judges.py
git commit -m "feat(timeboxing): elicit(): place, classify in one batch, rank, generate for the top three; the matrix fact rewritten whole

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: The host runs `elicit` in `resolve(SKELETON)`

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_host.py:196-236` (`_frame_from_corpus`) and the module imports
- Modify: `tests/unit/test_timeboxing_host_stage1_rows.py`

**Interfaces:**
- Consumes: `build_judges`, `elicit` from `elicitation_judges`.
- Produces: `PlanningContext(facts=[frame?, matrix_fact], applicable_constraints, suspended_constraint_count, probes)`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_timeboxing_host_stage1_rows.py`, add after the imports:

```python
import pytest

from fateforger.agents.timeboxing.elicitation import CoverageMatrix
from fateforger.agents.timeboxing.elicitation_judges import ElicitationResult, Judges
from fateforger.agents.timeboxing.session_contracts import ProbeDraft, coverage_fact_id


class _StubJudges:
    """`elicit` is replaced wholesale: the host's job is to call it with the
    rows it fetched and the snapshot it was given, and to carry the result."""

    def __init__(self) -> None:
        self.calls: list[tuple[PlanningSessionSnapshot, list[dict]]] = []

    async def __call__(self, snapshot, rows, judges, *, session_key, **_):  # noqa: ANN001
        self.calls.append((snapshot, rows))
        from fateforger.agents.timeboxing.elicitation import ALL_CELLS

        matrix = CoverageMatrix(cells={c.id: "not_applicable" for c in ALL_CELLS})
        fact = PlanningFact(fact_id=coverage_fact_id(snapshot.planning_day.date), kind=FactKind.COVERAGE_MATRIX, value=matrix.model_dump(mode="json"), source="system")
        return ElicitationResult(matrix_fact=fact, probes=[ProbeDraft(cell_id="elicit.body.unclear", question="q?", why_needed="w")])


@pytest.fixture(autouse=True)
def stub_elicit(monkeypatch):
    import fateforger.slack_bot.timeboxing_host as host_module

    stub = _StubJudges()
    monkeypatch.setattr(host_module, "elicit", stub)
    monkeypatch.setattr(host_module, "build_judges", lambda client: Judges(placement=None, coverage=None, probe=None))
    return stub
```

Change the existing test's last assertion from `assert context.facts == []` to:

```python
    assert [f.kind for f in context.facts] == [FactKind.COVERAGE_MATRIX]
    assert [p.cell_id for p in context.probes] == ["elicit.body.unclear"]
    assert stub_elicit.calls[0][1] == ROWS
```

and add `stub_elicit` to that test's parameters. Then append:

```python
def test_a_frame_the_judge_states_is_in_the_snapshot_elicit_sees(stub_elicit, monkeypatch) -> None:
    import fateforger.slack_bot.timeboxing_host as host_module

    class _Frame:
        def __init__(self, client) -> None:
            pass

        async def frame_on_record(self, *, day, constraints, session_key):
            return PlanningFact(fact_id=f"frame:{day.date.isoformat()}", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00", "basis": ["c1"]}, source="constraint_memory")

    monkeypatch.setattr("fateforger.agents.timeboxing.day_frame.DayFrameJudge", _Frame)
    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=object())
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))

    context = asyncio.run(host.resolve(_snapshot(), target=ArtifactKind.SKELETON, progress=_Sink()))

    assert [f.kind for f in context.facts] == [FactKind.DAY_FRAME, FactKind.COVERAGE_MATRIX]
    seen_snapshot, _ = stub_elicit.calls[0]
    assert any(f.kind is FactKind.DAY_FRAME for f in seen_snapshot.facts)


def test_no_model_client_is_a_dependency_failure_even_with_a_frame_stated() -> None:
    from fateforger.agents.timeboxing.adaptive_timeboxing import AdaptiveDependencyUnavailable

    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=None)
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00"}, source="user")
    with pytest.raises(AdaptiveDependencyUnavailable):
        asyncio.run(host.resolve(_snapshot(frame), target=ArtifactKind.SKELETON, progress=_Sink()))
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_timeboxing_host_stage1_rows.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'elicit'` from the monkeypatch.

- [ ] **Step 3: Wire the host**

In `src/fateforger/slack_bot/timeboxing_host.py`, add to the module imports:

```python
from fateforger.agents.timeboxing.elicitation_judges import build_judges, elicit
```

Replace `_frame_from_corpus` with:

```python
    async def _frame_from_corpus(
        self, snapshot: PlanningSessionSnapshot
    ) -> PlanningContext:
        """What memory says about the day, and what Stage 1 still needs to ask.

        The rules are returned in every case (#262). The frame judgement is
        skipped when the user typed a frame this session. Then `elicit` runs
        the three Stage 1 judgements against the rules and the snapshot --
        with the frame just judged merged in, so the `bounded` row sees it --
        and returns the matrix fact and the probes. A host that cannot judge
        fails the turn rather than proposing to close a stage it never opened.
        """
        planning_day = self._locked_day(snapshot)
        constraints = await self._active_constraints(planning_day)
        suspended = await self._suspended_count(planning_day)
        model_client = getattr(self._runtime, "timeboxing_intent_model_client", None)
        if model_client is None:
            raise AdaptiveDependencyUnavailable("no model client for the Stage 1 judgements")

        frame: PlanningFact | None = None
        if not any(fact.kind is FactKind.DAY_FRAME for fact in snapshot.facts):
            from fateforger.agents.timeboxing.day_frame import DayFrameJudge

            frame = await DayFrameJudge(model_client).frame_on_record(
                day=planning_day,
                constraints=constraints,
                session_key=snapshot.session_key,
            )
        seen = snapshot if frame is None else snapshot.model_copy(update={"facts": [*snapshot.facts, frame]})
        rows = constraints if isinstance(constraints, list) else []
        result = await elicit(seen, rows, build_judges(model_client), session_key=snapshot.session_key)
        return PlanningContext(
            facts=([frame] if frame is not None else []) + [result.matrix_fact],
            applicable_constraints=constraints,
            suspended_constraint_count=suspended,
            probes=result.probes,
        )
```

Add `PlanningFact` to the host's imports from `session_contracts` if it is not already there.

- [ ] **Step 4: Run the host tests and the integration route tests**

Run: `$PY -m pytest tests/unit/test_timeboxing_host_stage1_rows.py tests/unit -q -m "not slow" -p no:randomly -k "host or stage1 or timeboxing_route or harness_timeboxing"`
Expected: all PASS. If an integration test constructs a real `HostPlanningContext` with `timeboxing_intent_model_client=object()` and now fails inside `elicit`, give it the same `stub_elicit` autouse fixture (copy the class and fixture verbatim into that file).

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/timeboxing_host.py tests/unit
git commit -m "feat(slack): resolve(SKELETON) runs the Stage 1 judgements and carries the matrix and probes (#262)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: The kernel asks with the resolved probe

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py:605-611` (call site) and `:995-1018` (`_stage1_outcome`)
- Modify: `tests/unit/test_adaptive_stage1.py`

**Interfaces:**
- Changes: `_stage1_outcome(self, snapshot, readiness, probes: list[ProbeDraft])`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_adaptive_stage1.py`:

```python
class _ProbingContext(_Context):
    def __init__(self, matrix_fact, probes) -> None:
        self._fact = matrix_fact
        self._probes = probes

    async def resolve(self, snapshot, *, target, progress):
        return PlanningContext(facts=[self._fact], applicable_constraints=ROWS, suspended_constraint_count=3, probes=self._probes)


def _probing_kernel(snapshot, matrix_fact, probes):
    from fateforger.agents.timeboxing.session_contracts import ProbeDraft  # noqa: F401

    repository = InMemoryPlanningSessionRepository([snapshot])
    planner = _Planner()
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=_ProbingContext(matrix_fact, probes),
        commit=_Commit(),
    )
    return kernel, repository, planner


def test_the_resolved_probe_is_asked_instead_of_the_catalog_text() -> None:
    from fateforger.agents.timeboxing.session_contracts import BlockerOption, ProbeDraft

    cell = ALL_CELLS[0]
    probe = ProbeDraft(
        cell_id=cell.id,
        question="Still up at 07:00 on Tuesday?",
        why_needed="to bound the morning",
        options=[BlockerOption(option_id=f"{cell.id}:1", label="yes", effect="yes")],
    )
    kernel, repository, _ = _probing_kernel(_snapshot(), _matrix_fact(cell.id), [probe])
    outcome = _turn(kernel, _snapshot(), Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.question == "Still up at 07:00 on Tuesday?"
    assert outcome.why_needed == "to bound the morning"
    assert [o.option_id for o in outcome.options] == [f"{cell.id}:1"]
    held = _load(repository).pending_blocker
    assert held.requirement_id == cell.id and [o.option_id for o in held.options] == [f"{cell.id}:1"]
    assert all(f.kind is not FactKind.COVERAGE_MATRIX or "probes" not in str(f.value) for f in _load(repository).facts)


def test_a_probe_for_another_cell_falls_back_to_the_catalog_text() -> None:
    from fateforger.agents.timeboxing.session_contracts import ProbeDraft

    cell = ALL_CELLS[0]
    other = ProbeDraft(cell_id=ALL_CELLS[1].id, question="other?", why_needed="w")
    kernel, _, _ = _probing_kernel(_snapshot(), _matrix_fact(cell.id), [other])
    outcome = _turn(kernel, _snapshot(), Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.question != "other?"
    assert outcome.options == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_adaptive_stage1.py -q -k "resolved_probe or falls_back"`
Expected: FAIL — `outcome.question == "Still up at 07:00 on Tuesday?"` is false (the catalog text is asked).

- [ ] **Step 3: Thread the probes**

In `adaptive_timeboxing.py`, the run loop call site becomes:

```python
        if target is ArtifactKind.SKELETON and snapshot.stage1 != "closed":
            stage1_snapshot, stage1_outcome = self._stage1_outcome(
                snapshot, readiness, list(resolved.probes)
            )
```

and `_stage1_outcome` becomes:

```python
    def _stage1_outcome(
        self,
        snapshot: PlanningSessionSnapshot,
        readiness: ReadinessReport,
        probes: list[ProbeDraft],
    ) -> tuple[PlanningSessionSnapshot, TurnOutcome]:
        """What Stage 1 shows right now: the top open cell, or a proposal to close.

        The one place `stage1_gate` becomes a `TurnOutcome`, and the run
        loop -- after resolving context and holding any hard user blocker --
        is its only caller: `FileAssumption` and `GoBack` fall through to it
        rather than answering for it, so `GateMet` and the `stage1 ==
        "proposed"` transition happen exactly once per turn, in one place.
        `stage1_gate` itself already subtracts any cell a filed assumption
        answers, so the `Gate` read back here needs no further narrowing.

        `probes` are what the host's judges phrased this turn. The one whose
        cell is the top open cell is asked; otherwise the catalog's criterion
        text is, so a turn whose top cell could not be grounded still puts a
        question and the gate line says what is open.
        """
        gate: Gate = stage1_gate(snapshot)
        if gate.open_cells:
            top = gate.open_cells[0]
            gap = readiness.by_id(top.id)
            probe = next((p for p in probes if p.cell_id == top.id), None)
            options = list(probe.options) if probe is not None else []
            return self._hold_question(snapshot, gap, options), AwaitingUser(
                requirement_id=gap.requirement_id,
                question=probe.question if probe is not None else gap.question,
                why_needed=probe.why_needed if probe is not None else gap.why_needed,
                options=options,
                gate=gate,
            )
        return snapshot.model_copy(update={"stage1": "proposed"}), GateMet(gate=gate)
```

Check the other callers of `_stage1_outcome`: run `grep -n "_stage1_outcome(" src/fateforger/agents/timeboxing/adaptive_timeboxing.py`. Expected: only the run-loop call site above (the docstring says so); if another exists, pass `[]` there.

- [ ] **Step 4: Run the kernel tests**

Run: `$PY -m pytest tests/unit/test_adaptive_stage1.py tests/unit -q -m "not slow" -p no:randomly -k "adaptive or stage1 or elicit"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/unit/test_adaptive_stage1.py
git commit -m "feat(timeboxing): Stage 1 asks with the probe the host phrased for the top cell

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: #294 — the card reads the stage from the kernel's catalog

**Files:**
- Modify: `src/fateforger/agents/timeboxing/readiness.py:317-375`
- Modify: `src/fateforger/slack_bot/stage_cards.py:382-400`
- Modify: `src/fateforger/slack_bot/timeboxing_cards.py:875`
- Test: `tests/unit/test_stage_cards.py`

**Interfaces:**
- Changes: `TimeboxRequirements(catalog=None)`; `stage_of` and `target_of` become instance methods over `self._catalog`; `map_outcome(..., requirements: TimeboxRequirements | None = None)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_stage_cards.py`:

```python
def test_map_outcome_reads_the_stage_from_the_requirements_it_is_given() -> None:
    from dataclasses import replace

    from fateforger.agents.timeboxing.readiness import TimeboxRequirements, _CATALOG
    from fateforger.agents.timeboxing.session_contracts import AwaitingUser
    from fateforger.slack_bot.stage_cards import map_outcome
    # PendingTimeboxCandidates and _snapshot are already imported/defined at the top of this file.

    # A catalog where the activity question is filed under stage 4 -- a
    # different instance than the module default; the card must follow it.
    moved = tuple(replace(r, stage=4) if r.requirement_id == "skeleton.requested_activity" else r for r in _CATALOG)
    requirements = TimeboxRequirements(catalog=moved)
    outcome = AwaitingUser(requirement_id="skeleton.requested_activity", question="What?", why_needed="w")
    card = map_outcome(
        outcome, _snapshot(), pending=PendingTimeboxCandidates(), actor_user_id="U1",
        session_key="C1:1.0", channel_id="C1", thread_ts="1.0", requirements=requirements,
    )
    assert card is not None and card.stage.index == 4
```

`_snapshot(**update)` is defined at `tests/unit/test_stage_cards.py:47` and `PendingTimeboxCandidates` is imported at line 36 from `fateforger.slack_bot.timebox_candidate`.

- [ ] **Step 2: Run to verify it fails**

Run: `$PY -m pytest tests/unit/test_stage_cards.py -q -k reads_the_stage`
Expected: FAIL — `TypeError: TimeboxRequirements() takes no arguments` or `map_outcome() got an unexpected keyword argument 'requirements'`.

- [ ] **Step 3: Give the catalog an instance**

In `readiness.py`, in `TimeboxRequirements` add a constructor and make `target_of` and `stage_of` instance methods:

```python
    def __init__(self, catalog: tuple[ArtifactRequirement, ...] | None = None) -> None:
        #: The module catalog unless a caller hands another; the kernel and
        #: the card must read the same one (#294).
        self._catalog = _CATALOG if catalog is None else catalog
```

Replace `for requirement in _CATALOG` with `for requirement in self._catalog` inside `evaluate`, `target_of` and `stage_of`, and drop `@staticmethod` from `target_of` and `stage_of` (add `self`). Then:

Run: `grep -rn "TimeboxRequirements\.\(stage_of\|target_of\)" src tests`
Expected hits: `src/fateforger/slack_bot/stage_cards.py:400` and possibly tests. Change each to an instance call.

In `stage_cards.py`, `map_outcome` gains a keyword `requirements: TimeboxRequirements | None = None` and line 400 becomes:

```python
        index = (requirements or TimeboxRequirements()).stage_of(outcome.requirement_id)
```

In `timeboxing_cards.py:875`, the caller passes `requirements=` through if it has a `requirements` value in scope; if it does not (check with `grep -n "requirements" src/fateforger/slack_bot/timeboxing_cards.py`), leave the call unchanged — the default is the module catalog the kernel also constructs, which is the production behaviour #294 records as correct.

- [ ] **Step 4: Run the surface and kernel tests**

Run: `$PY -m pytest tests/unit tests/integration -q -m "not slow" -p no:randomly -k "stage_cards or adaptive or readiness or harness_timeboxing or stage1"`
Expected: all PASS. If `tests/integration/test_harness_timeboxing_session_route.py` carries a monkeypatch workaround for #294 (search for `stage_of` in it), remove the workaround and pass `requirements=` instead.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/readiness.py src/fateforger/slack_bot/stage_cards.py src/fateforger/slack_bot/timeboxing_cards.py tests
git commit -m "fix(slack): the card reads a question's stage from the requirements instance it is given (#294)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: The composability proof (#286)

**Files:**
- Create: `tests/unit/test_elicitation_composes.py`
- Modify: `tests/unit/test_stage_context.py` (one test)

**Interfaces:**
- Consumes: `elicit`, `Judges`, `stage1_gate`, the kernel harness from `test_adaptive_stage1.py`.

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_elicitation_composes.py`:

```python
"""The seam between the coverage spec and the probe voice (#286).

Three swaps, each leaving the other half untouched. If the gate still decides
with the words stubbed, and the words still render with the spec stubbed, the
loop composes: the concern-floor and the phrasing can change on different
schedules, which is what makes a growing anchor layer safe.
"""
from __future__ import annotations

import asyncio
from datetime import date

from fateforger.agents.timeboxing.elicitation import ALL_CELLS, CoverageMatrix, stage1_gate
from fateforger.agents.timeboxing.elicitation_judges import Judges, Placement, elicit
from fateforger.agents.timeboxing.session_contracts import (
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProbeDraft,
)
from tests.fixtures.stage1.days import FIXTURE_DAYS, snapshot_for

GYM = {"uid": "a-gym", "name": "gym"}
ROWS = [
    {"uid": "c-oats", "name": "Oats before gym", "description": "Eat oats two hours before the gym.", "necessity": "must", "anchors": [GYM]},
    {"uid": "c-exit", "name": "Block exit criteria", "description": "Every block ends with an exit criterion.", "necessity": "must", "anchors": []},
]


class _Placement:
    async def place(self, *, anchors, unanchored_rules, session_key):  # noqa: ANN001
        return Placement(anchors={"a-gym": "body"}, rules={"c-exit": "method"})


class _Coverage:
    def __init__(self, table: dict[str, str]) -> None:
        self.table = table

    async def classify(self, *, cell, rules, stated, request, session_key):  # noqa: ANN001
        return self.table.get(cell.id, "covered"), "stub"


class _FixedWords:
    """The words half, stubbed to one string for every cell."""

    async def generate(self, *, cell, rules_full, conversation, request, session_key):  # noqa: ANN001
        return ProbeDraft(cell_id=cell.id, question="Tell me more?", why_needed="fixed")


class _NoWords:
    async def generate(self, *, cell, rules_full, conversation, request, session_key):  # noqa: ANN001
        return None


def _with_matrix(snapshot: PlanningSessionSnapshot, fact: PlanningFact) -> PlanningSessionSnapshot:
    return snapshot.model_copy(update={"facts": [*snapshot.facts, fact]})


def _run(snapshot, judges):
    return asyncio.run(elicit(snapshot, ROWS, judges, session_key=snapshot.session_key))


def test_swap_one_the_gate_decides_the_same_with_the_words_stubbed() -> None:
    table = {"elicit.body.unclear": "uncovered", "elicit.method.contradictory": "uncovered"}
    for day in FIXTURE_DAYS:
        snapshot = snapshot_for(day, ROWS)
        with_words = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage(table), probe=_FixedWords()))
        without = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage(table), probe=_NoWords()))
        gate_a = stage1_gate(_with_matrix(snapshot, with_words.matrix_fact))
        gate_b = stage1_gate(_with_matrix(snapshot, without.matrix_fact))
        assert [c.id for c in gate_a.open_cells] == [c.id for c in gate_b.open_cells]
        assert gate_a.open_cells, day.key
        closed = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage({}), probe=_NoWords()))
        assert stage1_gate(_with_matrix(snapshot, closed.matrix_fact)).open_cells == []


def test_swap_two_the_words_render_with_the_spec_stubbed_to_one_cell() -> None:
    """Fixed uncovered cell, real ranking, real probe carrying: the draft
    reaches the result and names the cell the spec opened."""
    snapshot = snapshot_for(FIXTURE_DAYS[0], ROWS)
    result = _run(snapshot, Judges(placement=_Placement(), coverage=_Coverage({"elicit.body.tacit_knowledge": "uncovered"}), probe=_FixedWords()))
    assert [p.cell_id for p in result.probes] == ["elicit.body.tacit_knowledge"]
    assert result.probes[0].question == "Tell me more?"


def test_swap_three_everything_unplaced_still_opens_the_gate_and_drops_nothing() -> None:
    class _Unplaced:
        async def place(self, *, anchors, unanchored_rules, session_key):  # noqa: ANN001
            return Placement(anchors={a["uid"]: "unplaced" for a in anchors}, rules={r["uid"]: "unplaced" for r in unanchored_rules})

    snapshot = snapshot_for(FIXTURE_DAYS[0], ROWS)
    result = _run(snapshot, Judges(placement=_Unplaced(), coverage=_Coverage({}), probe=_NoWords()))
    matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
    assert matrix.rows["unplaced"].rule_count == 2
    assert stage1_gate(_with_matrix(snapshot, result.matrix_fact)).open_cells == []
```

In `tests/unit/test_stage_context.py`, append (the card-side seam `admonish-1-52` asked for):

```python
def test_a_rule_placed_under_method_ranks_as_open_when_a_method_cell_is_uncovered(monkeypatch) -> None:
    import fateforger.slack_bot.stage_context as module

    class Matrix:
        cells = {"elicit.method.contradictory": "uncovered"}
        placement = {"a-gym": "method"}

    monkeypatch.setattr(module, "coverage_matrix", lambda snapshot: Matrix())
    rows = [_row("c1", DINNER, fade=0.9), _row("c2", GYM, fade=None)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=None)
    assert ranked[0].uid == "c2" and ranked[0].open_concern is True
```

- [ ] **Step 2: Run them**

Run: `$PY -m pytest tests/unit/test_elicitation_composes.py tests/unit/test_stage_context.py -q`
Expected: all PASS. (These are proofs over code that exists; if swap one fails, the gate is reading the words somewhere — find it before going on, it is the finding #286 asked for.)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_elicitation_composes.py tests/unit/test_stage_context.py
git commit -m "test(timeboxing): the gate does not depend on the words and the words do not depend on the spec (#286)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: The anchor relink (#290)

**Files:**
- Create: `scripts/memory/__init__.py` (empty), `scripts/memory/relink_anchors.py`

**Interfaces:**
- Produces: `relink(db_path, judge, *, apply) -> list[RelinkReport]`; CLI `relink_anchors.py <db> [--apply]`.

- [ ] **Step 1: Write the script**

Create `scripts/memory/relink_anchors.py`:

```python
"""Link the durable rules that predate the anchor graph to the anchors their
observations already name (#290).

`reproject` cannot do this: anchors are written to the append-only log at
ingest and re-projection re-asks projection only (I2). The split path already
relinks from observation anchor names; this is the same three calls as a
one-off pass. Resolving a name to an anchor is a judgement, so it goes to the
judge even on a dry run; nothing is minted (`max_new=0`): a name the judge
cannot place on an existing anchor is reported, not created.

Run on a copy first, then on the live store; `--apply` backs the store up
before writing. Links only: no observation, no projection, no other field.

    PYTHONPATH=src python scripts/memory/relink_anchors.py /path/copy.db
    PYTHONPATH=src python scripts/memory/relink_anchors.py data/memory.db --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date

from dotenv import load_dotenv

from memory.anchor_store import AnchorStore
from memory.anchoring import resolve_anchors
from memory.constraint_store import ConstraintStore
from memory.judge import Judge
from memory.openrouter_judge import OpenRouterJudge
from memory.store import ObservationStore


@dataclass
class RelinkReport:
    uid: str
    name: str
    names: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    #: "linked" (or "would link" on a dry run), "no names" for a rule whose
    #: observations name no anchor, "unresolved" when the judge would mint
    action: str = ""


async def relink(db_path: str, judge: Judge, *, apply: bool) -> list[RelinkReport]:
    observations = ObservationStore(db_path)
    constraints = ConstraintStore(db_path)
    anchors = AnchorStore(db_path)
    reports: list[RelinkReport] = []
    for constraint in constraints.durable():
        if anchors.anchors_for(constraint.uid):
            continue
        report = RelinkReport(uid=constraint.uid, name=constraint.name)
        names: list[str] = []
        for observation_uid in constraints.observations_for(constraint.uid):
            observation = observations.get(observation_uid)
            if observation is not None:
                names.extend(observation.anchors)
        report.names = list(dict.fromkeys(names))
        if not report.names:
            report.action = "no names"
            reports.append(report)
            continue
        try:
            report.resolved = await resolve_anchors(report.names, anchors, judge, max_new=0)
        except ValueError as exc:
            report.action = f"unresolved: {exc}"
            reports.append(report)
            continue
        if apply:
            anchors.replace_constraint_links(constraint.uid, report.resolved)
            report.action = "linked"
        else:
            report.action = "would link"
        reports.append(report)
    return reports


def _judge() -> Judge:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set; load the parent .env (see the plan)")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return OpenRouterJudge(api_key=api_key, base_url=base_url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db", help="the memory store to read (and, with --apply, write)")
    parser.add_argument("--apply", action="store_true", help="write the links; backs the store up first")
    args = parser.parse_args()
    load_dotenv()
    if args.apply:
        backup = f"{args.db}.bak-{date.today().isoformat()}-pre-relink"
        shutil.copy(args.db, backup)
        print(f"backup: {backup}")
    reports = asyncio.run(relink(args.db, _judge(), apply=args.apply))
    for report in reports:
        print(f"{report.uid[:8]}  {report.action:<14} {report.name}")
        if report.names:
            print(f"          names: {report.names}")
            print(f"          anchors: {report.resolved}")
    linked = sum(1 for r in reports if r.action in ("linked", "would link"))
    print(f"\n{len(reports)} unanchored durable rules: {linked} {'linked' if args.apply else 'would link'}, "
          f"{sum(1 for r in reports if r.action == 'no names')} with no names, "
          f"{sum(1 for r in reports if r.action.startswith('unresolved'))} unresolved")


if __name__ == "__main__":
    main()
```

Create `scripts/memory/__init__.py` as an empty file.

- [ ] **Step 2: Dry run on a copy**

The worktree has no `.env`; the parent does. Run:

```bash
cp /Users/hugoevers/VScode-projects/admonish-1/data/memory.db /private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/1fb469ec-85a6-4b73-957f-71cd24f96fea/scratchpad/relink-copy.db
set -a; source /Users/hugoevers/VScode-projects/admonish-1/.env; set +a
$PY scripts/memory/relink_anchors.py /private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/1fb469ec-85a6-4b73-957f-71cd24f96fea/scratchpad/relink-copy.db
```

Expected: 14 unanchored durable rules reported; 8 "would link" with names among `deep work`, `lunch`, `reading`, `dinner`, `shower`; 6 "no names" — *Block exit criteria*, *Deep-work entry criteria gate*, *Artifact-first scheduling gate*, *C2F framing cap*, *No morning meetings*, *Revenue/outreach duration cap*. If the count differs, the store moved since the findings doc (2026-09-04); record the actual numbers and continue only if every "would link" resolution reads as correct.

- [ ] **Step 3: Apply on the copy and check**

```bash
$PY scripts/memory/relink_anchors.py /private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/1fb469ec-85a6-4b73-957f-71cd24f96fea/scratchpad/relink-copy.db --apply
sqlite3 /private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/1fb469ec-85a6-4b73-957f-71cd24f96fea/scratchpad/relink-copy.db "select count(*) from constraints c where c.tier='durable' and not exists (select 1 from constraint_anchors ca where ca.constraint_uid=c.uid)"
```

Expected: `6`.

- [ ] **Step 4: Ask before the live store, then apply**

Tell Hugo the dry-run numbers and ask for the go. Then:

```bash
$PY scripts/memory/relink_anchors.py /Users/hugoevers/VScode-projects/admonish-1/data/memory.db --apply
```

Expected: the backup path printed, then the same 8/6 split. If `admonish-1-76` has said their v5 migration is imminent, apply the relink first anyway — it writes only `constraint_anchors` rows, which every schema version carries.

- [ ] **Step 5: Commit the script**

```bash
git add scripts/memory/__init__.py scripts/memory/relink_anchors.py
git commit -m "feat(memory): a one-off pass links pre-graph durable rules to the anchors their observations name (#290)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: Freeze the fixture and retire the labels

**Files:**
- Modify: `tests/fixtures/stage1/days.py`
- Delete: `tests/fixtures/stage1/labels.toml`
- Modify: `tests/evals/test_stage1_fixture.py`
- Modify: `.gitignore` (if `data/fixtures/` is not already covered by the `data/memory.db*` rule)

**Interfaces:**
- Produces: `FIXTURE_STORE_SHA256`, `verify_store(db_path) -> None`; `rows_for` refuses a mismatched store.

- [ ] **Step 1: Freeze**

```bash
mkdir -p /Users/hugoevers/VScode-projects/admonish-1/data/fixtures
cp /Users/hugoevers/VScode-projects/admonish-1/data/memory.db /Users/hugoevers/VScode-projects/admonish-1/data/fixtures/stage1-$(date +%Y%m%d).db
shasum -a 256 /Users/hugoevers/VScode-projects/admonish-1/data/fixtures/stage1-$(date +%Y%m%d).db
git -C /Users/hugoevers/VScode-projects/admonish-1 check-ignore data/fixtures/stage1-$(date +%Y%m%d).db || echo "NOT IGNORED"
```

If the last line prints `NOT IGNORED`, add `data/fixtures/` to `.gitignore` in the worktree and re-check. Never `git add` the db.

- [ ] **Step 2: Write the failing test**

In `tests/evals/test_stage1_fixture.py`, replace `test_every_day_has_hand_labels_before_a_spike_may_run` with:

```python
def test_a_store_whose_bytes_moved_is_refused(store_copy, tmp_path) -> None:
    """The evals measure against one frozen store. A copy that differs -- a
    relink, a migration, a new rule -- silently changes every matrix, so the
    hash is pinned and a mismatch fails here, not in a number nobody trusts."""
    from tests.fixtures.stage1.days import verify_store

    verify_store(store_copy)
    drifted = tmp_path / "drifted.db"
    drifted.write_bytes(Path(store_copy).read_bytes() + b"\x00")
    with pytest.raises(RuntimeError, match="sha256"):
        verify_store(str(drifted))
```

and remove the `LABELS` constant and `load_labels` import.

- [ ] **Step 3: Pin the hash and delete the labels**

In `tests/fixtures/stage1/days.py`, replace the module docstring's last two sentences with: `The store is frozen: FIXTURE_STORE_SHA256 pins its bytes and rows_for refuses any other, so an eval never silently measures against a moved corpus. Ground truth is constructed by ablation (tests/evals/test_stage1_elicitation.py), not hand-labelled.`

Add after the imports:

```python
import hashlib

#: sha256 of data/fixtures/stage1-<date>.db, the post-relink copy frozen on
#: <date>. Re-freezing is deliberate: bump this and re-run the evals.
FIXTURE_STORE_SHA256 = "<paste the shasum output from Step 1>"


def verify_store(db_path: str) -> None:
    digest = hashlib.sha256(Path(db_path).read_bytes()).hexdigest()
    if digest != FIXTURE_STORE_SHA256:
        raise RuntimeError(
            f"fixture store sha256 {digest[:12]}… does not match the pinned "
            f"{FIXTURE_STORE_SHA256[:12]}…; the evals measure against one frozen store"
        )
```

Paste the real digest. In `rows_for`, add `verify_store(db_path)` as the first line. Delete `LabelledGap`, `load_labels`, and `import tomllib`. Delete `tests/fixtures/stage1/labels.toml`.

- [ ] **Step 4: Run the fixture eval against the frozen store**

```bash
STAGE1_FIXTURE_DB=/Users/hugoevers/VScode-projects/admonish-1/data/fixtures/stage1-$(date +%Y%m%d).db $PY -m pytest tests/evals/test_stage1_fixture.py -q -m slow
$PY -m pytest tests/unit/test_stage1_fixture_shapes.py -q
```

Expected: all PASS (4 in the eval file: three days plus the hash test). If `test_each_day_yields_rows_and_a_locked_snapshot` fails on `verify_store` because the KG client wrote to the copy on open (a WAL file or a `user_version` bump), pin the hash of the copy *after* one open instead and note that in the constant's comment.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/stage1/days.py tests/evals/test_stage1_fixture.py .gitignore
git rm tests/fixtures/stage1/labels.toml
git commit -m "test(stage1): the fixture store is frozen and pinned by hash; hand labels retired for ablation

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: The golden days

**Files:**
- Create: `tests/fixtures/stage1/golden.toml`
- Modify: `tests/fixtures/stage1/days.py`

**Interfaces:**
- Produces: `load_golden(path) -> dict[str, list[str]]`; `GOLDEN = Path(__file__).parent / "golden.toml"`.

- [ ] **Step 1: Draft the golden days from the frozen store's rules**

Read the working Tuesday's rows once to draft from what is on record (names only):

```bash
STAGE1_FIXTURE_DB=/Users/hugoevers/VScode-projects/admonish-1/data/fixtures/stage1-$(date +%Y%m%d).db $PY - <<'EOF'
import os
from tests.fixtures.stage1.days import FIXTURE_DAYS, rows_for
for row in rows_for(os.environ["STAGE1_FIXTURE_DB"], FIXTURE_DAYS[0]):
    print(row["necessity"], "|", row["name"], "|", row["description"][:90])
EOF
```

Create `tests/fixtures/stage1/golden.toml` with facts consistent with those rules. Starting draft — **Hugo corrects this before the first eval run** (Task 16, Step 5):

```toml
# The facts a good coach would end up with about each fixture day. The
# simulated user answers only from these; they are the eval's script, not
# ground truth for any judgement. Hugo corrects them before the first run.

[working_tuesday]
facts = [
  "Up at 07:00, asleep by 23:30.",
  "Deep work runs 09:00 to 11:30 in one block, then a second 90-minute block after lunch if the morning went well.",
  "Lunch at 12:30 for 45 minutes, at home.",
  "Gym at 18:00 for 75 minutes; oats at 16:00 so they are two hours before.",
  "Dinner at home around 19:45; cooking takes 30 minutes.",
  "No commute: working from home all day.",
  "One fixed call at 15:00 for 30 minutes that cannot move.",
  "The C2F framing work is capped at one 60-minute block today.",
]

[vacation_day]
facts = [
  "Up at 08:30, no bedtime target.",
  "Gym at 18:00 for 75 minutes still holds; nothing else is scheduled.",
  "Deep work in the morning means one 60-minute block on personal writing, not client work.",
]

[sunday]
facts = [
  "Up at 08:00, asleep by 23:00.",
  "Gym at 18:00 for 60 minutes; dinner after, at 19:30.",
  "Deep work in the morning means 90 minutes of reading, not screen work.",
]
```

- [ ] **Step 2: Load it**

Append to `tests/fixtures/stage1/days.py` (restore `import tomllib`):

```python
GOLDEN = Path(__file__).resolve().parent / "golden.toml"


def load_golden(path: Path = GOLDEN) -> dict[str, list[str]]:
    """Per fixture day, the facts the simulated user may answer from. A day
    with no golden facts fails loudly: a simulated user with nothing to say
    would make every probe a nuisance and the measure meaningless."""
    raw = tomllib.loads(path.read_text())
    golden = {key: [str(line) for line in section.get("facts", [])] for key, section in raw.items()}
    for day in FIXTURE_DAYS:
        if not golden.get(day.key):
            raise ValueError(f"{day.key} has no golden facts in {path}")
    return golden
```

- [ ] **Step 3: Test the loader**

Append to `tests/unit/test_stage1_fixture_shapes.py`:

```python
def test_every_fixture_day_has_golden_facts() -> None:
    from tests.fixtures.stage1.days import FIXTURE_DAYS, load_golden

    golden = load_golden()
    assert set(golden) == {d.key for d in FIXTURE_DAYS}
    assert len(golden["working_tuesday"]) >= 6
```

Run: `$PY -m pytest tests/unit/test_stage1_fixture_shapes.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/stage1/golden.toml tests/fixtures/stage1/days.py tests/unit/test_stage1_fixture_shapes.py
git commit -m "test(stage1): golden days: the simulated user's script, drafted from the frozen store for Hugo to correct

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: The evals — simulated user, turns-to-gate, ablation

**Files:**
- Create: `tests/evals/test_stage1_elicitation.py`

**Interfaces:**
- Consumes: `elicit`, `build_judges`, `stage1_gate`, `ranked_open_cells`, `FIXTURE_DAYS`, `rows_for`, `snapshot_for`, `load_golden`, `build_autogen_chat_client`.
- Produces: `SimulatedUser`, `run_stage1(day, rows, judges, user, golden, *, cap=12) -> Trace`, `Trace(turns, calls)`.

- [ ] **Step 1: Write the eval module**

Create `tests/evals/test_stage1_elicitation.py`:

```python
"""Stage 1 elicitation quality against the frozen fixture, on a real model.

Two instruments, neither a hand label. The recall floor is constructed by
ablation: delete a fact the golden day states, and the cell that fact belongs
to must come back `uncovered` and be the first asked. The headline is turns to
`GateMet` with a simulated user who answers only from the golden day. The
simulated user and the framing judge run on a model that is not the contender
(`NON_CONTENDER_MODEL`): if they shared the judges' lineage, turns-to-gate
would measure two copies of one judgement agreeing.

n = 5 draws per case, in parallel; every assertion is on a rate.

    STAGE1_FIXTURE_DB=data/fixtures/stage1-<date>.db \
      PYTHONPATH=src .venv/bin/python -m pytest tests/evals/test_stage1_elicitation.py -m slow -q
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest
from dotenv import load_dotenv

from fateforger.agents.timeboxing.elicitation import CoverageMatrix, ranked_open_cells, stage1_gate
from fateforger.agents.timeboxing.elicitation_judges import Judges, build_judges, elicit
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlannerAssumption,
    PlanningFact,
    PlanningSessionSnapshot,
    ProbeDraft,
    elicited_fact_id,
)
from tests.fixtures.stage1.days import FIXTURE_DAYS, FixtureDay, load_golden, rows_for, snapshot_for

pytestmark = pytest.mark.slow

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

N = 5
#: The simulated user and the framing judge must not share the contender's
#: lineage. The pro-tier pin is a different model family from the flash tier
#: the judges run on; the literal is the last fallback only, never the first
#: choice (project pins moved off gemini on 2026-08-24; see PR #312).
NON_CONTENDER_MODEL = (
    os.environ.get("STAGE1_NON_CONTENDER_MODEL")
    or os.environ.get("OPENROUTER_DEFAULT_MODEL_PRO")
    or "deepseek/deepseek-v4-pro-0813:nitro"
)
TURNS_P50_TUESDAY = 4
CAP = 12


# ---------------------------------------------------------------- clients


def _contender():
    from fateforger.llm.factory import build_autogen_chat_client

    return build_autogen_chat_client("timeboxing_agent")


def _non_contender():
    from fateforger.llm.factory import build_autogen_chat_client

    return build_autogen_chat_client("timeboxing_agent", model=NON_CONTENDER_MODEL)


@pytest.fixture(scope="module")
def store_copy(tmp_path_factory) -> str:
    source = os.environ.get("STAGE1_FIXTURE_DB", "").strip()
    if not source:
        pytest.skip("STAGE1_FIXTURE_DB not set; the evals need the frozen store")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    target = tmp_path_factory.mktemp("stage1") / "memory.db"
    shutil.copy(source, target)
    return str(target)


# ---------------------------------------------------------- simulated user

from autogen_core.models import SystemMessage, UserMessage  # noqa: E402
from pydantic import BaseModel, ConfigDict  # noqa: E402


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["answered", "not_relevant", "already_said"]
    text: str


_USER_PROMPT = """You are playing a specific person being asked questions by
their day-planning coach. You know ONLY the facts listed under "golden" and
what you have already said this session under "said". Answer the question in
one or two plain sentences, in the first person.

kind "answered": the golden facts let you answer; give the answer. kind
"already_said": you already said this earlier -- say so briefly. kind
"not_relevant": nothing in the golden facts bears on the question, or it asks
about something that does not exist for this day -- say you don't know or it
doesn't apply. Never invent a fact that is not in golden. Return only the
requested schema.
"""


class SimulatedUser:
    def __init__(self, model_client) -> None:
        self.model_client = model_client

    async def answer(self, probe: ProbeDraft, golden: list[str], said: list[str]) -> _Reply:
        prompt = json.dumps({"question": probe.question, "golden": golden, "said": said}, ensure_ascii=False)
        result = await self.model_client.create(
            [SystemMessage(content=_USER_PROMPT), UserMessage(content=prompt, source="user")], json_output=_Reply
        )
        return _Reply.model_validate_json(result.content)


class _Framing(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    addresses: bool


class FramingJudge:
    """Does the probe ask about the fact that was removed? Non-contender."""

    def __init__(self, model_client) -> None:
        self.model_client = model_client

    async def addresses(self, probe: ProbeDraft, removed: str) -> bool:
        prompt = json.dumps({"question": probe.question, "removed_fact": removed}, ensure_ascii=False)
        result = await self.model_client.create(
            [
                SystemMessage(content="Would answering this question supply the removed fact, or the part of it that is missing? Answer only the schema."),
                UserMessage(content=prompt, source="user"),
            ],
            json_output=_Framing,
        )
        return _Framing.model_validate_json(result.content).addresses


# ---------------------------------------------------------------- the loop


@dataclass
class Turn:
    cell: str
    question: str | None
    reply: str  # answered | not_relevant | already_said | assumed
    #: distinct judge batches this turn: classify, generate, and placement
    #: when it ran. One batch of concurrent calls is one round-trip.
    round_trips: int = 0


@dataclass
class Trace:
    turns: list[Turn] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # judge call labels, in order
    gate_met: bool = False

    @property
    def probes(self) -> int:
        return sum(1 for t in self.turns if t.reply != "assumed")


class _Counting:
    """Wraps a judge so the trace can count round-trips: one batch of
    concurrent calls is one trip."""

    def __init__(self, inner, label: str, log: list[str]) -> None:
        self._inner = inner
        self._label = label
        self._log = log

    def __getattr__(self, name):
        method = getattr(self._inner, name)

        async def call(*args, **kwargs):
            self._log.append(self._label)
            return await method(*args, **kwargs)

        return call


def _merge(snapshot: PlanningSessionSnapshot, fact: PlanningFact) -> PlanningSessionSnapshot:
    by_id = {f.fact_id: f for f in snapshot.facts}
    by_id[fact.fact_id] = fact
    return snapshot.model_copy(update={"facts": list(by_id.values())})


async def run_stage1(
    day: FixtureDay, rows: list[dict[str, Any]], judges: Judges, user: SimulatedUser, golden: list[str], *, cap: int = CAP
) -> Trace:
    trace = Trace()
    counted = Judges(
        placement=_Counting(judges.placement, "place", trace.calls),
        coverage=_Counting(judges.coverage, "classify", trace.calls),
        probe=_Counting(judges.probe, "generate", trace.calls),
    )
    snapshot = snapshot_for(day, rows)
    said: list[str] = []
    for _ in range(cap):
        before = len(trace.calls)
        result = await elicit(snapshot, rows, counted, session_key=snapshot.session_key)
        snapshot = _merge(snapshot, result.matrix_fact)
        gate = stage1_gate(snapshot)
        if not gate.open_cells:
            trace.gate_met = True
            break
        top = gate.open_cells[0]
        probe = next((p for p in result.probes if p.cell_id == top.id), None)
        labels = trace.calls[before:]
        trips = len({label for label in labels})  # distinct batches this turn
        if probe is None:
            # Nothing groundable: the person would assume past it.
            snapshot = snapshot.model_copy(
                update={"assumptions": [*snapshot.assumptions, PlannerAssumption(assumption_id=f"as-{top.id}", requirement_id=top.id, value="assumed", why_needed="unaskable", filed_by="user")]}
            )
            turn = Turn(cell=top.id, question=None, reply="assumed", round_trips=trips)
        else:
            reply = await user.answer(probe, golden, said)
            said.append(reply.text)
            snapshot = _merge(
                snapshot,
                PlanningFact(fact_id=elicited_fact_id(top.id), kind=FactKind.ELICITED_STATEMENT, value={"cell": top.id, "text": reply.text}, source="user"),
            )
            turn = Turn(cell=top.id, question=probe.question, reply=reply.kind, round_trips=trips)
        trace.turns.append(turn)
    return trace


# ---------------------------------------------------------------- measures


def _traces(day: FixtureDay, store_copy: str, golden: dict[str, list[str]]) -> list[Trace]:
    rows = rows_for(store_copy, day)
    contender = _contender()
    user = SimulatedUser(_non_contender())

    async def all_draws():
        return await asyncio.gather(*(run_stage1(day, rows, build_judges(contender), user, golden[day.key]) for _ in range(N)))

    return asyncio.run(all_draws())


@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=[d.key for d in FIXTURE_DAYS])
def test_the_gate_is_reached_and_the_tuesday_takes_at_most_four_probes_at_p50(day, store_copy) -> None:
    traces = _traces(day, store_copy, load_golden())
    reached = sum(1 for t in traces if t.gate_met)
    print(f"\n{day.key}: gate met {reached}/{N}; probes per draw {[t.probes for t in traces]}")
    for t in traces:
        print("  ", [(turn.cell, turn.reply) for turn in t.turns])
    assert reached >= 4, f"{day.key}: gate met in only {reached}/{N} draws within {CAP} turns"
    if day.key == "working_tuesday":
        assert statistics.median(t.probes for t in traces) <= TURNS_P50_TUESDAY


def test_nuisance_and_re_ask_rates_on_the_tuesday(store_copy) -> None:
    traces = _traces(FIXTURE_DAYS[0], store_copy, load_golden())
    turns = [turn for t in traces for turn in t.turns if turn.reply != "assumed"]
    nuisance = sum(1 for turn in turns if turn.reply == "not_relevant")
    re_asked = sum(1 for turn in turns if turn.reply == "already_said")
    print(f"\nprobes {len(turns)}; not_relevant {nuisance}; already_said {re_asked}")
    assert re_asked == 0
    assert nuisance <= max(1, len(turns) // 5)


def test_at_most_two_round_trips_per_probe_at_p50(store_copy) -> None:
    traces = _traces(FIXTURE_DAYS[0], store_copy, load_golden())
    trips = [turn.round_trips for t in traces for turn in t.turns]
    assert statistics.median(trips) <= 2


# ---------------------------------------------------------------- ablation


@dataclass(frozen=True)
class Ablation:
    key: str
    removed: str
    #: transform (rows, request, golden) -> (rows, request, extra facts)
    apply: Any
    #: expected cell id, or a function of the matrix for a placement-dependent row
    expected: Any


def _strip_gym_time(rows, request, golden):
    return rows, "deep work in the morning, gym", []


def _fixed_morning_meeting(rows, request, golden):
    extra = [PlanningFact(fact_id=elicited_fact_id(None), kind=FactKind.ELICITED_STATEMENT, value={"cell": None, "text": "There is a fixed meeting at 09:00 for an hour."}, source="user")]
    return rows, request, extra


def _drop_dinner_rows(rows, request, golden):
    # Membership over anchor uids the memory server minted (filled in Step 2),
    # never a comparison over the anchor's name.
    kept = [r for r in rows if not any(a.get("uid") in _DINNER_UIDS for a in (r.get("anchors") or []))]
    return kept, request, []


_DINNER_UIDS: set[str] = set()


def _deep_work_row(matrix: CoverageMatrix) -> str:
    """The row the deep-work anchor was placed under, read from the matrix."""
    for uid, row in matrix.placement.items():
        if uid in _DEEP_WORK_UIDS:
            return f"elicit.{row}.tacit_knowledge"
    raise AssertionError("no deep-work anchor in the placement")


_DEEP_WORK_UIDS: set[str] = set()


ABLATIONS = [
    Ablation("no_gym_time", "gym at 18:00", _strip_gym_time, "elicit.request.tacit_knowledge"),
    Ablation("fixed_09_meeting_vs_no_morning_meetings", "No morning meetings", _fixed_morning_meeting, "elicit.method.contradictory"),
]


@pytest.mark.parametrize("case", ABLATIONS, ids=[a.key for a in ABLATIONS])
def test_a_removed_or_conflicting_fact_opens_its_cell_first_and_the_probe_addresses_it(case, store_copy) -> None:
    day = FIXTURE_DAYS[0]
    rows, request, extra = case.apply(rows_for(store_copy, day), day.request, load_golden()[day.key])
    base = snapshot_for(FixtureDay(day.key, day.date, day.day_type, request), rows)
    snapshot = base.model_copy(update={"facts": [*base.facts, *extra]})
    contender = _contender()
    framing = FramingJudge(_non_contender())

    async def one():
        result = await elicit(snapshot, rows, build_judges(contender), session_key=snapshot.session_key)
        matrix = CoverageMatrix.model_validate(result.matrix_fact.value)
        expected = case.expected(matrix) if callable(case.expected) else case.expected
        ranked = ranked_open_cells(matrix)
        first = ranked[0].id if ranked else None
        probe = next((p for p in result.probes if p.cell_id == expected), None)
        addressed = await framing.addresses(probe, case.removed) if probe else False
        return matrix.cells.get(expected), first == expected, addressed

    draws = asyncio.run(asyncio.gather(*(one() for _ in range(N))))
    uncovered = sum(1 for state, _, _ in draws if state == "uncovered")
    first = sum(1 for _, is_first, _ in draws if is_first)
    addressed = sum(1 for _, _, ok in draws if ok)
    print(f"\n{case.key}: uncovered {uncovered}/{N}; first {first}/{N}; addressed {addressed}/{N}")
    assert uncovered >= 4
    assert first >= 4
    assert addressed >= 4


def test_removing_every_dinner_rule_makes_the_dinner_row_not_applicable(store_copy) -> None:
    day = FIXTURE_DAYS[0]
    full = rows_for(store_copy, day)
    rows, request, _ = _drop_dinner_rows(full, day.request, [])
    snapshot = snapshot_for(day, rows)
    contender = _contender()

    async def one():
        result = await elicit(snapshot, rows, build_judges(contender), session_key=snapshot.session_key)
        return CoverageMatrix.model_validate(result.matrix_fact.value)

    matrices = asyncio.run(asyncio.gather(*(one() for _ in range(N))))
    assert _DINNER_UIDS, "fill _DINNER_UIDS from the frozen store (Step 2)"
    for matrix in matrices:
        assert not (_DINNER_UIDS & set(matrix.placement)), "a dinner anchor was placed with no dinner rule present"
```

Note on `_DEEP_WORK_UIDS`: the "drop the deep-work duration" ablation from the spec depends on the deep-work anchor's uid in the frozen store. Fill the set in Step 3 from the store, then add the case:

```python
def _drop_deep_work_duration(rows, request, golden):
    return rows, request, []  # the golden line with the duration is simply not stated; nothing to remove from rows


ABLATIONS.append(Ablation("no_deep_work_duration", "deep work block duration", _drop_deep_work_duration, _deep_work_row))
```

- [ ] **Step 2: Fill the deep-work uid**

```bash
sqlite3 /Users/hugoevers/VScode-projects/admonish-1/data/fixtures/stage1-$(date +%Y%m%d).db "select uid, name from anchors order by name"
```

Read the list and paste the uid(s) of the deep-work anchor(s) into `_DEEP_WORK_UIDS` and of the dinner anchor(s) into `_DINNER_UIDS`. The operator picks by eye from the printed names; the test then compares uids only.

- [ ] **Step 3: Confirm the suite still skips cleanly without the env**

Run: `$PY -m pytest tests/evals/test_stage1_elicitation.py -q -m slow`
Expected: every test SKIPPED with "STAGE1_FIXTURE_DB not set".

- [ ] **Step 4: Run the ablation cases first (cheap, single-turn)**

```bash
set -a; source /Users/hugoevers/VScode-projects/admonish-1/.env; set +a
STAGE1_FIXTURE_DB=/Users/hugoevers/VScode-projects/admonish-1/data/fixtures/stage1-$(date +%Y%m%d).db $PY -m pytest tests/evals/test_stage1_elicitation.py -q -m slow -k "removed_or_conflicting or dinner" -s
```

Expected: PASS with the printed rates. If `uncovered < 4/5` on a case, the classify prompt is not seeing the gap: read the `why` strings (add a temporary print of the judge's `why` in `elicit`'s `_one`, remove before commit) before touching the prompt, and resample after any change — a fix validated by one passing draw is not validated.

- [ ] **Step 5: Ask Hugo to correct `golden.toml`, then run the loop evals**

Show Hugo `tests/fixtures/stage1/golden.toml` and take his corrections. Then:

```bash
STAGE1_FIXTURE_DB=/Users/hugoevers/VScode-projects/admonish-1/data/fixtures/stage1-$(date +%Y%m%d).db $PY -m pytest tests/evals/test_stage1_elicitation.py -q -m slow -k "gate_is_reached or nuisance or round_trips" -s
```

Expected: PASS with printed traces. The likely failure is `gate met < 4/5` because some cell never flips `covered` — the printed per-turn `(cell, reply)` lists show which. That is the "alternatives never covered" shape from the findings doc; the fix is in `_COVERAGE_PROMPT`'s discriminator for that criterion, validated by resampling, never in the gate.

- [ ] **Step 6: Commit**

```bash
git add tests/evals/test_stage1_elicitation.py tests/fixtures/stage1/golden.toml
git commit -m "test(evals): Stage 1 elicitation measured by ablation and turns-to-GateMet with a non-contender simulated user

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 17: Record the numbers

**Files:**
- Create: `docs/superpowers/research/2026-09-0X-stage1-loop-evals.md` (use the real date)

- [ ] **Step 1: Write the note from the printed output of Task 16**

```markdown
# Stage 1 loop: the first measurements

Run <date> against `data/fixtures/stage1-<date>.db` (sha256 <first 12>), contender
`<the model build_autogen_chat_client("timeboxing_agent") resolved — print it>` at
`minimal`, simulated user and framing judge on `<NON_CONTENDER_MODEL>`, n = 5.

The 2026-09-04 spike figures (27/29 placement unanimity, 23/35 cells uncovered,
`alternatives` never covering) were measured on `google/gemini-3.6-flash`, which the
project had already left on 2026-08-24; they are that model's numbers and are not
compared against here.

## Turns to GateMet

| day | gate met | probes per draw | p50 |
| --- | --- | --- | --- |
| working Tuesday | ?/5 | [?, ?, ?, ?, ?] | ? |
| vacation day | ?/5 | [...] | ? |
| Sunday | ?/5 | [...] | ? |

## Nuisance and re-ask (Tuesday, pooled)

probes ?; not_relevant ? (?%); already_said ?.

## Round-trips per probe

p50 ?.

## Ablation

| case | uncovered | first asked | probe addresses it |
| --- | --- | --- | --- |
| no gym time | ?/5 | ?/5 | ?/5 |
| fixed 09:00 vs No morning meetings | ?/5 | ?/5 | ?/5 |
| no deep-work duration | ?/5 | ?/5 | ?/5 |

## What moved during the run

<every prompt change made to reach the thresholds, with the before/after rates>
```

Fill every `?` from the `-s` output. No `?` may survive.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/research/
git commit -m "docs(research): Stage 1 loop eval numbers on the frozen fixture

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 18: Whole suite, rebase, PR, tickets, restart, live

**Files:** none in `src/` unless a failure demands it.

- [ ] **Step 1: The package suite**

Run: `$PY -m pytest tests -q -m "not slow" -p no:randomly`
Expected: 0 failed. (The three failures on main before this branch — two weekend-sensitive, one #281 — are fixed by Task 1.)

- [ ] **Step 2: Rebase on main**

```bash
git fetch origin
git rebase origin/main
$PY -m pytest tests -q -m "not slow" -p no:randomly
```

Expected: clean rebase (or conflicts only in test files touched by #310's routing change — resolve by keeping both), suite green.

- [ ] **Step 3: Push and open the PR**

```bash
git push -u origin feat/stage1-elicitation-loop
gh pr create --title "Stage 1 asks: the judges that fill the coverage matrix, the method concern, ablation evals (#262)" --body "$(cat <<'EOF'
## Problem

Nothing wrote the coverage matrix, so `stage1_gate` opened on the first turn: Stage 1 locked the day, showed the rules, and proposed to close without asking anything — #262's original symptom behind a consent step.

## What this does

- `PlacementJudge`, `CoverageJudge`, `ProbeJudge` on the `DayFrameJudge` pattern, and `elicit()`: place → classify (one parallel batch) → rank (existing arithmetic) → generate for the top three. The host runs it in `resolve(SKELETON)`; the kernel asks with the resolved probe. The gate is untouched.
- The seventh concern `method` (how the day gets planned); 45 cells.
- #290: the anchor relink, run on the live store (backup taken).
- Evals: hand-labels retired for an ablation floor and turns-to-`GateMet` with a non-contender simulated user. Numbers: `docs/superpowers/research/…-stage1-loop-evals.md`.
- #286 closed by three stub swaps; #294 fixed; the two stale tests on main fixed.

Spec: `docs/superpowers/specs/2026-09-05-stage1-elicitation-loop-design.md`. Plan: `docs/superpowers/plans/2026-09-05-stage1-elicitation-loop.md`.

## Rubric

- [ ] `pytest tests -m "not slow"`: 0 failed
- [ ] ablation: every case ≥ 4/5 uncovered, first, addressed
- [ ] Tuesday reaches `GateMet` ≥ 4/5, ≤ 4 probes at p50; re-ask 0
- [ ] `demo.py status` after restart: no STALE-CODE

## Human checklist

- [ ] Hugo corrected `golden.toml` before the loop evals ran
- [ ] One live Tuesday session in Slack: Stage 1 asks, Next appears only after the gate line is empty

Closes #262, #284, #286, #290, #294. Overtakes #285.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Tickets**

```bash
gh issue close 297 --comment "Fixed on main by 932ddfe: the decided list names suspensions and elicited statements (stage_cards._decided)."
gh issue close 276 --comment "Closed by the catalog: every ArtifactRequirement carries stage, the card reads it, and _QUESTION_STAGE is gone (#295)."
gh issue close 285 --comment "Overtaken: Phase 1 (#295) put the matrix in the snapshot and the gate in the kernel, which is the premise B rejects. The loop is built host-side per the design's own plan section; see the feat/stage1-elicitation-loop PR."
gh issue comment 283 --body "Re-purposed: the fixture stays, the hand labels are retired for an ablation floor and turns-to-GateMet with a non-contender simulated user (spec 2026-09-05-stage1-elicitation-loop-design.md). Numbers in docs/superpowers/research."
gh issue comment 262 --body "Phase 1 landed (#295, #301). The loop that fills the matrix is the feat/stage1-elicitation-loop PR."
gh issue create --title "timeboxing: the agent addressed in a thread with no open session starts one" --body "Found by admonish-1-34 while fixing the planning-thread routing (#310): the timeboxing agent starts a fresh 5-stage session for any message reaching it in a thread with no open session, rather than saying it has nothing open. Routing is what broke on 2026-09-05 03:43; this trigger-happiness is what made it loud. Entry policy, not elicitation — out of the Stage 1 loop's scope."
```

Post the new issue's number to `admonish-1-34` if that session is still listed by `ListAgents`.

- [ ] **Step 5: After merge — restart the stack and run one live session**

After Hugo merges:

```bash
cd /Users/hugoevers/VScode-projects/admonish-1 && git pull --ff-only && .venv/bin/python scripts/demo.py restart && .venv/bin/python scripts/demo.py status
```

Expected: memory and slack-bot both HEALTHY at the merge sha, no STALE-CODE. Then Hugo starts a timeboxing session for a working Tuesday in Slack: Stage 1 shows the rules, asks a probe, and the Next control appears only once the gate line is empty. That is the human checklist item; record what happened on the PR.
