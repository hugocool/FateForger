# Applicability Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `Applicability` at write time so structurally-scoped rules stop being served on days they do not apply to.

**Architecture:** The `tier` judgement already reads the whole statement and already returns `label` and `is_declaration`; applicability rides along in the same call, so this costs no extra round-trip. `TierJudgement` carries the three raw fields (`start_date`, `end_date`, `days_of_week`); `projection` maps them into the existing `Applicability` value object. The read path is untouched — it already filters on applicability, arithmetically, with no model in the path.

**Tech Stack:** Python 3.11, pydantic v2, pytest 8. No new dependencies.

**Ticket:** #151. **Spec:** `docs/superpowers/specs/2026-08-16-kg-memory-server-design.md`

**Why now:** measured on the seeded store, `Client attendance days` ("go to client on Tuesdays and Thursdays") is returned for a Monday, as are `Wednesday revenue-first precedence` and `Systems-work quarantine before Friday`. `projection` writes an unconstrained `Applicability()` on every constraint, so a filter that works correctly never has anything to filter on. This is the cheapest available reduction of the 36-constraint Monday flood and it is orthogonal to the anchor graph.

## Global Constraints

- **No keyword matching, string matching, or regex. `import re` is banned.** Reading "Tuesdays and Thursdays" out of a sentence is a judgement about meaning and belongs entirely to the model. Nothing in this plan may inspect constraint text.
- **No model call in the read path.** `get_active_constraints` is unchanged.
- **`judge.py` must not import from `constraint.py`.** The port stays free of knowledge about the layer above it — hence raw fields on `TierJudgement`, mapped in `projection`.
- **The stub's default must stay unconstrained.** A forgotten stub must produce a rule that applies every day, never one that silently applies on no day.
- `from __future__ import annotations`; type annotations on public functions; zero `fateforger.*` imports; tests in `tests/memory/`; never create `tests/memory/__init__.py` or `tests/__init__.py`.
- Extraction model `google/gemini-3.6-flash`, `"reasoning": {"effort": "minimal"}`.

## What already exists

At the branch tip: `TierJudgement` (fields `tier`, `is_declaration`, `label`, `rationale`), `IngestResult` (`stored`, `uid`, `tier`, `anchors`, `suppressed_as`, `label`, `is_declaration`), `Applicability` in `constraint.py` with `applies_on(day)`, `projection.project` constructing `Applicability()` at two sites, `StubJudge` with `tiers`/`labels`/`declarations` dicts. 83 unit tests, 12 eval tests.

---

### Task 1: Carry applicability from the judgement to the constraint

**Files:**
- Modify: `src/memory/judge.py` (`TierJudgement`, `StubJudge`)
- Modify: `src/memory/openrouter_judge.py` (`TIER_PROMPT`)
- Modify: `src/memory/ingest.py` (`IngestResult`)
- Modify: `src/memory/projection.py` (both construction sites)
- Test: `tests/memory/test_applicability_extraction.py`

**Interfaces:**
- Produces: `TierJudgement.start_date: date | None`, `.end_date: date | None`, `.days_of_week: list[int]`; the same three on `IngestResult`; `StubJudge(days_of_week={text: [1,3]}, start_dates={...}, end_dates={...})`.

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_applicability_extraction.py
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import StubJudge, TierJudgement
from memory.models import Channel, Observation, Provenance, Tier
from memory.projection import project
from memory.read_api import get_active_constraints

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
MONDAY = date(2026, 8, 17)
TUESDAY = date(2026, 8, 18)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def test_tier_judgement_defaults_to_unconstrained():
    """A rule with no stated scoping must apply every day, never no day."""
    j = TierJudgement(label="x")
    assert j.start_date is None
    assert j.end_date is None
    assert j.days_of_week == []


def test_stub_returns_canned_days_of_week():
    judge = StubJudge(days_of_week={"client on Tue and Thu": [1, 3]})
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        judge.tier(_obs("client on Tue and Thu"))
    )
    assert result.days_of_week == [1, 3]


async def test_days_of_week_reaches_the_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("go to client on Tuesdays and Thursdays")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Client attendance days", is_declaration=True,
        days_of_week=[1, 3],
    )
    c = await project(obs, result, StubJudge(), store)
    assert c.applicability.days_of_week == [1, 3]


async def test_a_tuesday_thursday_rule_is_not_served_on_monday(tmp_path):
    """The measured defect: this rule was returned for a Monday."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("go to client on Tuesdays and Thursdays")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Client attendance days", is_declaration=True,
        days_of_week=[1, 3],
    )
    await project(obs, result, StubJudge(), store)
    assert get_active_constraints(store, MONDAY) == []
    assert len(get_active_constraints(store, TUESDAY)) == 1


async def test_a_date_range_reaches_the_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("this sprint, cap framing at 15 minutes")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Framing cap", is_declaration=True,
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 14),
    )
    c = await project(obs, result, StubJudge(), store)
    assert c.applicability.start_date == date(2026, 8, 1)
    assert c.applicability.end_date == date(2026, 8, 14)
    assert get_active_constraints(store, MONDAY) == []


async def test_an_unscoped_rule_still_applies_every_day(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("sleep at 23:00")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE, label="Sleep schedule",
    )
    await project(obs, result, StubJudge(), store)
    assert len(get_active_constraints(store, MONDAY)) == 1
    assert len(get_active_constraints(store, TUESDAY)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/test_applicability_extraction.py -v`
Expected: FAIL — `TierJudgement` has no `start_date`, `IngestResult` has no `days_of_week`.

- [ ] **Step 3: Write minimal implementation**

In `src/memory/judge.py`, add `from datetime import date` and extend `TierJudgement`:

```python
    # Applicability rides along on this judgement rather than getting its own
    # call: deciding durability already requires reading the whole statement,
    # so the scoping words are in front of the model anyway. Raw fields rather
    # than the Applicability value object, so this port stays free of any
    # import from the constraint layer above it.
    start_date: date | None = None
    end_date: date | None = None
    days_of_week: list[int] = Field(default_factory=list)  # 0=Mon .. 6=Sun
```

Extend `StubJudge.__init__` with `days_of_week`, `start_dates`, `end_dates` dicts (all `dict[str, ...] | None = None`), stored as `self._days_of_week` / `self._start_dates` / `self._end_dates`, and have `tier` pass them through:

```python
            start_date=self._start_dates.get(observation.text),
            end_date=self._end_dates.get(observation.text),
            days_of_week=self._days_of_week.get(observation.text, []),
```

In `src/memory/openrouter_judge.py`, extend `TIER_PROMPT` — keep everything it already asks for, and add:

```
Also say when the rule applies, if the statement scopes it:
- days_of_week: weekday numbers it is limited to, Monday=0 through Sunday=6.
  Only when the statement names particular days. A rule that holds every day
  gets an empty list.
- start_date / end_date: ISO dates, only when the statement names a period.
  A standing rule gets null for both.

Do not invent scoping. "Sleep at 23:00" applies every day: empty list, null
dates. "Go to client on Tuesdays and Thursdays" is [1, 3].
```

and extend the JSON contract line to:
`{"tier": ..., "is_declaration": ..., "label": ..., "days_of_week": [...], "start_date": null, "end_date": null, "rationale": "..."}`

In `src/memory/ingest.py`, add the same three fields to `IngestResult` (same defaults) and populate them from `tier_j` on the success path.

In `src/memory/projection.py`, replace `Applicability()` at **both** construction sites with:

```python
            applicability=Applicability(
                start_date=ingest_result.start_date,
                end_date=ingest_result.end_date,
                days_of_week=ingest_result.days_of_week,
            ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/memory/ -v -m "not slow"`
Expected: 89 passed (6 new + 83 existing)

- [ ] **Step 5: Commit**

```bash
git add src/memory/judge.py src/memory/openrouter_judge.py src/memory/ingest.py \
        src/memory/projection.py tests/memory/test_applicability_extraction.py
git commit -m "feat(memory): extract applicability at write time"
```

---

### Task 2: Eval the applicability prompt against the real model

Unit tests prove the plumbing. Only an eval proves the model reads scoping correctly — and, just as importantly, that it does **not** invent scoping for rules that have none.

**Files:**
- Modify: `tests/memory/test_eval_extraction.py`

- [ ] **Step 1: Write the eval tests**

Append, following the existing `async with _judge() as judge:` pattern:

```python
async def test_named_weekdays_are_extracted():
    """Measured defect: this rule was served on a Monday."""
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Client attendance days: Go to client on Tuesdays and Thursdays.")
        )
    assert sorted(result.days_of_week) == [1, 3]


async def test_a_single_named_weekday_is_extracted():
    async with _judge() as judge:
        result = await judge.tier(
            _obs(
                "Wednesday revenue-first precedence: On Wednesday, Revenue lane "
                "must run before any build/system cognitive block."
            )
        )
    assert result.days_of_week == [2]


async def test_a_daily_rule_acquires_no_day_filter():
    """The dangerous direction: inventing scoping silently hides a rule."""
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Sleep schedule: Aim to sleep at 23:00 and wake at 07:00.")
        )
    assert result.days_of_week == []
    assert result.start_date is None
    assert result.end_date is None


async def test_an_unscoped_rule_acquires_no_dates():
    async with _judge() as judge:
        result = await judge.tier(
            _obs("Oats Timing: Oats must be consumed exactly 2 hours before the gym.")
        )
    assert result.days_of_week == []
    assert result.start_date is None
```

- [ ] **Step 2: Run the evals**

```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.worktrees/memory-observation-log
set -a && . /Users/hugoevers/VScode-projects/admonish-1/.env && set +a
PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python \
  -m pytest tests/memory/test_eval_extraction.py -v -m slow
```
Expected: **16 passed** (4 new + 12 existing). The existing tier evals must still pass — the prompt grew, and a regression there means the addition displaced what was already working.

If an eval fails, iterate on the prompt wording and report how many rounds it took. **Never weaken an assertion** — `test_a_daily_rule_acquires_no_day_filter` in particular is the one that matters most, because inventing scoping silently hides a real rule, which is worse than the flood it is meant to reduce.

- [ ] **Step 3: Commit**

```bash
git add tests/memory/test_eval_extraction.py
git commit -m "test(memory): eval applicability extraction against the live model"
```

---

### Task 3: Re-seed and measure the flood reduction

- [ ] **Step 1: Re-seed over the fixed pipeline**

```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.worktrees/memory-observation-log
mv data/memory.db data/memory.db.pre-applicability
set -a && . /Users/hugoevers/VScode-projects/admonish-1/.env && set +a
PYTHONPATH=src /Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python \
  -m memory.backfill /Users/hugoevers/VScode-projects/admonish-1/data/admonish.db \
  data/memory.db
```

- [ ] **Step 2: Measure**

Report, with numbers: the Monday count before (36) and after; how many of the 38 constraints acquired any applicability; and — spot-checked by hand — whether `Client attendance days`, `Wednesday revenue-first` and `Systems-work quarantine` now carry the right days, and whether anything acquired scoping it should not have.

- [ ] **Step 3: Append the numbers to the findings doc**

Add a short section to `docs/superpowers/research/2026-08-17-seeding-run-findings.md` recording the before/after, then commit. Do not commit `data/memory.db*` — it is gitignored and carries real user data.

---

## Not in this plan

- **Decay** (#152) — the other half of the flood reduction, and a grilling ticket because the class vocabulary must be decided against the corpus rather than invented.
- The anchor graph, semantic relevance, MCP sampling.
