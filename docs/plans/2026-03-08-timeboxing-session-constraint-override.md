# Timeboxing Session Constraint Override Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make same-day explicit session constraints suppress conflicting default/profile memory in the timeboxing agent.

**Architecture:** Keep the fix in the timeboxing constraint-selection path. Reuse existing `aspect_classification` metadata to compute same-day active aspect context, suppress conditionally inapplicable defaults, then run the existing family-reconciliation ranking on the remaining candidates.

**Tech Stack:** Python 3.11, pytest, SQLModel, Pydantic, AutoGen timeboxing agent

---

### Task 1: Add Failing Regressions For Session Override And Conditional Suppression

**Files:**
- Modify: `tests/unit/test_timeboxing_constraint_selection.py`
- Reference: `src/fateforger/agents/timeboxing/agent.py`

**Step 1: Write the failing tests**

Add tests that cover:

```python
async def test_session_activity_overrides_conflicting_profile_default() -> None:
    ...

async def test_conditional_default_is_suppressed_by_same_day_activity() -> None:
    ...

async def test_conditional_default_remains_when_no_blocker_exists() -> None:
    ...
```

Each test should build `Constraint` objects with `hints["aspect_classification"]` metadata and call `_collect_constraints()` or `_reconcile_constraints_for_stage_context()` through a minimal `TimeboxingFlowAgent` fixture pattern already used in this test module.

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_timeboxing_constraint_selection.py -q`

Expected: FAIL on the new override/suppression assertions because current reconciliation does not honor conditional activity blockers.

**Step 3: Write minimal implementation**

Do not implement yet; move to Task 2.

**Step 4: Run test to verify it still isolates the gap**

Run: `poetry run pytest tests/unit/test_timeboxing_constraint_selection.py -q`

Expected: the new tests fail and existing tests remain meaningful.

**Step 5: Commit**

Do not commit in this repo unless explicitly requested by the user.

### Task 2: Implement Same-Day Conditional Suppression In Constraint Selection

**Files:**
- Modify: `src/fateforger/agents/timeboxing/agent.py`
- Reference: `src/fateforger/agents/timeboxing/preferences.py`
- Reference: `src/fateforger/agents/timeboxing/nlu.py`

**Step 1: Add helper functions for active aspect context**

Add focused helpers in `agent.py` for:

```python
def _constraint_excluded_aspect_ids(constraint: Constraint) -> set[str]:
    ...

def _constraint_conditional_present_ids(constraint: Constraint) -> set[str]:
    ...

def _constraint_conditional_absent_ids(constraint: Constraint) -> set[str]:
    ...

def _collect_active_aspect_ids(constraints: list[Constraint]) -> set[str]:
    ...
```

These helpers should read only structured metadata from `hints["aspect_classification"]`.

**Step 2: Apply conditional suppression before family reconciliation**

Update `_reconcile_constraints_for_stage_context()` so it:

```python
active_aspect_ids = self._collect_active_aspect_ids(constraints)
eligible, suppressed = self._suppress_conditionally_inapplicable_constraints(
    session=session,
    constraints=constraints,
    active_aspect_ids=active_aspect_ids,
)
```

Then reconcile `eligible` by family/rank.

**Step 3: Preserve stronger session facts**

Ensure session-scoped explicit constraints are not suppressed by weaker profile/default constraints. Scope precedence should remain:

- `SESSION`
- `DATESPAN`
- `PROFILE`

When a conflict exists, the lower-precedence candidate should be suppressed or lose reconciliation.

**Step 4: Run targeted tests**

Run: `poetry run pytest tests/unit/test_timeboxing_constraint_selection.py -q`

Expected: PASS for the new regressions and the existing selection tests.

**Step 5: Commit**

Do not commit in this repo unless explicitly requested by the user.

### Task 3: Add Deterministic Debug Evidence For Suppression

**Files:**
- Modify: `src/fateforger/agents/timeboxing/agent.py`
- Test: `tests/unit/test_timeboxing_constraint_selection.py`

**Step 1: Write the failing observability assertion**

Add or extend a test asserting that the `constraints_active_snapshot` event contains suppression evidence, for example:

```python
assert snapshots[-1]["active_suppressed_count"] == 1
assert snapshots[-1]["active_suppressed_reasons"] == ["excluded_by_aspect"]
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/unit/test_timeboxing_constraint_selection.py -q`

Expected: FAIL because the snapshot does not yet expose suppression fields.

**Step 3: Implement the minimal debug fields**

Add deterministic suppression fields to `_collect_constraints()` / reconciliation helpers and keep values bounded and structured.

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/unit/test_timeboxing_constraint_selection.py -q`

Expected: PASS.

**Step 5: Commit**

Do not commit in this repo unless explicitly requested by the user.

### Task 4: Run The Broader Targeted Validation Subset

**Files:**
- Test: `tests/unit/test_timeboxing_constraint_selection.py`
- Test: `tests/unit/test_timeboxing_durable_constraints.py`
- Test: `tests/unit/test_constraint_retriever.py`
- Test: `tests/unit/test_timeboxing_memory_backend_selection.py`

**Step 1: Run the focused regression suite**

Run:

```bash
poetry run pytest \
  tests/unit/test_timeboxing_constraint_selection.py \
  tests/unit/test_timeboxing_durable_constraints.py \
  tests/unit/test_constraint_retriever.py \
  tests/unit/test_timeboxing_memory_backend_selection.py \
  -q
```

Expected: PASS.

**Step 2: Inspect for unintended behavioral drift**

Review failures or changed assertions for:
- active count semantics
- durable stage prefetch behavior
- Graphiti backend selection

**Step 3: Update issue checkpoint**

Post a progress checkpoint to issue `#91` with:
- tests run
- current status
- remaining risks
- `Open Items`

**Step 4: Re-run cleanliness check**

Run: `git status --porcelain`

Expected: only intended files for `#91` are modified.

**Step 5: Commit**

Do not commit in this repo unless explicitly requested by the user.
