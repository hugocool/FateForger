# Stage 1 First: The Shape of the Day, a Clock, and a Readable Gate Line — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stage 1 runs before the planner asks what the day is for; the Stage 1 judges know what time it is; the gate line says which rows are open instead of a comma soup.

**Architecture:** Three independent fixes on one branch. (1) The kernel's run loop consults the Stage 1 branch before `first_hard_user_blocker()`, so an auto-started session elicits the shape of the day first and the Priorities question comes once Stage 1 closes — the ladder the Stage 1 spec already states, `1, 1, …, 2, 3`. (2) `elicit()` takes a required tz-aware `now` which the host derives from its injectable clock in the planning timezone; both Stage 1 judges receive it and their prompts say what to do with it. (3) `_gate_line` groups open cells by row — arithmetic over row and criterion keys this system minted.

**Tech Stack:** Python 3.11, pydantic v2 strict models, `zoneinfo`, pytest (`-m "not slow"` for the gate; `slow` for the one eval).

**Tickets:** #411 (ordering), #412 (clock), #413 (gate line). **Rulings (Hugo, 2026-09-09):** the shape of the day is the most important unblocking thing and comes first; the planner should know what time it is.

## Global Constraints

- **No keyword matching, string matching, or regex against user content. Ever.** Nothing here resolves "in 2 hours" — the model does, given a clock. Grouping the gate line is arithmetic over `ROWS`/`CRITERIA` keys this system minted.
- **The read path never calls a model.** `_gate_line` and `map_outcome` stay synchronous and structural.
- **A failure must be loud.** A naive `datetime` handed to `elicit` raises; there is no default clock inside `elicit`.
- **Never assert an exact model output string in a unit test.** Stub the judges; assert what they were handed and what decision followed.
- **Evals sample n ≥ 8 and assert on a rate.** One passing draw proves nothing.
- **Work happens in `.claude/worktrees/stage1-first` on `feat/stage1-first`**, based on `feat/stage-card-whole` (PR #397). **The PR is a draft, base `main`, held until #397 merges.**
- **The gate before every commit is the package suite:** `$PY -m pytest tests -q -m "not slow" -p no:randomly`. Zero failures expected — #397's tree is fully green after its merge with main.
- **Every new test is broken on purpose before it is trusted.** Change the implementation so it fails, confirm, restore.
- **Commit after every task.** Messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

Shell alias, set once per shell:

```bash
cd /Users/hugoevers/VScode-projects/admonish-1/.claude/worktrees/stage1-first
export PYTHONPATH=src
PY=/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python
```

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | the run loop consults Stage 1 before the hard-blocker check | 1 |
| `tests/unit/test_adaptive_stage1.py` | the ladder: Stage 1 probe first, Priorities after close | 1 |
| `src/fateforger/agents/timeboxing/elicitation_judges.py` | `elicit(..., now=)`, both judges take `now`, both prompts say what it is for | 2 |
| `src/fateforger/slack_bot/timeboxing_host.py` | derive `now` in the planning tz from `self._now()` and pass it | 2 |
| `tests/fixtures/stage1/elicit_stub.py` | the shared stub records `now` | 2 |
| `tests/unit/test_elicitation_judges.py`, `tests/unit/test_timeboxing_host_stage1_rows.py` | stubs gain `now`; the host test asserts the tz conversion | 2 |
| `tests/evals/test_stage1_clock.py` | the "in 2 hours at 10:09" resample | 2 |
| `src/fateforger/slack_bot/stage_cards.py` | `_gate_line` groups by row | 3 |
| `tests/unit/test_stage_cards.py` | the two gate-line tests rewritten | 3 |

---

### Task 1: Stage 1 runs before the Priorities question

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py:698-726` (the run loop after `resolve`)
- Modify: `tests/unit/test_adaptive_stage1.py:296-322` (the test that pins the old order)
- Test: `tests/unit/test_adaptive_stage1.py`

**Interfaces:**
- Consumes: `readiness.first_hard_user_blocker()`, `self._stage1_outcome(snapshot, readiness, probes)`, `snapshot.stage1`.
- Produces: no new names. The observable contract: with `target is SKELETON` and `stage1 != "closed"`, the outcome is Stage 1's (`AwaitingUser` with `gate` set, or `GateMet`) even when a hard user blocker is open. Once `stage1 == "closed"`, the hard-blocker check runs exactly as today.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_adaptive_stage1.py`, using its existing `_snapshot`, `_kernel`, `_turn`, `_load`, `_matrix_fact` helpers (`_snapshot()` seeds `REQUESTED_ACTIVITY` and `DAY_FRAME`; override `facts=` to remove them):

```python
def test_the_shape_of_the_day_is_asked_before_the_priorities_question() -> None:
    """An auto-started session has said nothing. Stage 1 asks about the day's
    shape first; "what do you want out of the day" waits until it closes.
    Ruled 2026-09-09 (#411): the ladder is 1, 1, …, 2, 3 by construction."""
    cell = ALL_CELLS[0]
    snapshot = _snapshot(
        facts=[
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME,
                         value={"wake": "07:00", "sleep": "23:30"}, source="user"),
            _matrix_fact(cell.id),          # one Stage 1 cell open, no request
        ]
    )
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == cell.id
    assert outcome.gate is not None
    assert planner.briefs == []


def test_the_priorities_question_is_still_asked_once_stage_one_closes() -> None:
    """The hard user blocker is guaranteed before a skeleton; it is only asked
    later, not never."""
    snapshot = _snapshot(
        stage1="closed",
        facts=[
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME,
                         value={"wake": "07:00", "sleep": "23:30"}, source="user"),
        ],
    )
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.requested_activity"
    assert outcome.gate is None
    assert planner.briefs == []


def test_a_missing_frame_with_no_rule_to_probe_it_is_asked_after_stage_one_closes() -> None:
    """With nothing on record about the frame and no request, Stage 1 has no
    row to ground a probe in; it proposes to close, and the frame question
    comes from the catalog after consent rather than being lost."""
    snapshot = _snapshot(facts=[_matrix_fact(None)])   # nothing open, no frame, no request
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot, Advance())
    assert isinstance(outcome, GateMet)
    assert _load(repository).stage1 == "proposed"
    outcome = _turn(kernel, _load(repository), Advance())       # consent
    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.requested_activity"
```

Rewrite `test_file_assumption_for_a_cell_still_holds_a_missing_hard_blocker` (`:296`) — its premise ("the run loop holds a hard user blocker before it ever reaches Stage 1") is the defect. Under the new order a `FileAssumption` on the only open cell closes the gate:

```python
def test_file_assumption_for_the_last_open_cell_proposes_to_close_before_any_blocker() -> None:
    """`FileAssumption` falls through to Stage 1, which now runs before the
    hard-blocker check: forcing past the last open cell proposes to close,
    and the missing request is asked after consent, not instead of the stage."""
    cell = ALL_CELLS[0]
    snapshot = _snapshot(
        facts=[
            PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME,
                         value={"wake": "07:00", "sleep": "23:30"}, source="user"),
            _matrix_fact(cell.id),
        ]
    )
    kernel, repository, planner = _kernel(snapshot)
    outcome = _turn(kernel, snapshot,
                    FileAssumption(requirement_id=cell.id, value="assume a normal day", why_needed="user forced past"))
    assert isinstance(outcome, GateMet)
    assert _load(repository).stage1 == "proposed"
    assert planner.briefs == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_adaptive_stage1.py -v -p no:randomly`
Expected: the three new tests FAIL — the outcome is `AwaitingUser(requirement_id="skeleton.requested_activity")` where a Stage 1 outcome is expected. The rewritten one FAILS the same way.

- [ ] **Step 3: Implement**

In the run loop, move the Stage 1 branch above the hard-blocker check. `readiness` is computed once and both branches read it:

```python
        readiness = self._requirements.evaluate(target, snapshot)

        # Stage 1 before the hard-blocker check, deliberately. The catalog's
        # user-owned hard requirements -- the request, the frame -- are still
        # guaranteed before a skeleton is drafted, because this branch is
        # skipped once `stage1 == "closed"` and the check below then runs as
        # it always did. What changes is the order: the shape of the day is
        # elicited first, and "what do you want out of the day" is asked when
        # the stage closes. The other order asked the Priorities question on
        # every auto-started session before a single probe, and the ladder
        # read 1 -> 2 -> 1 (#411, #276). The Stage 1 spec states this ladder
        # as `1, 1, ..., 2, 3 by construction`; the loop now matches it.
        if target is ArtifactKind.SKELETON and snapshot.stage1 != "closed":
            stage1_snapshot, stage1_outcome = self._stage1_outcome(
                snapshot, readiness, list(resolved.probes)
            )
            return await self._save(
                stage1_snapshot,
                base_revision=base_revision,
                request=request,
                outcome=stage1_outcome,
            )

        blocker = readiness.first_hard_user_blocker()
        if blocker is not None:
            # (existing body unchanged)
```

Update `_stage1_outcome`'s docstring, which says the run loop calls it *"after resolving context and holding any hard user blocker"* — it now runs before that check.

- [ ] **Step 4: Run to verify they pass, then the package suite**

Run: `$PY -m pytest tests/unit/test_adaptive_stage1.py -v -p no:randomly`
Expected: PASS.
Run: `$PY -m pytest tests -q -m "not slow" -p no:randomly`
Expected: any failure is a test that seeded a fresh session (`stage1 == "open"`, no matrix fact) with a hard blocker open and asserted the blocker came first. Each such test is asserting the defect; update its expectation to the Stage 1 outcome, or give its snapshot `stage1="closed"` if the test is about something downstream of Stage 1 and never cared about the ladder. Say which you did for each in the report. Do not reorder the loop back.

- [ ] **Step 5: Break it on purpose**

Move the Stage 1 branch back below the blocker check. Confirm the three new tests and the rewritten one fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/
git commit -m "fix(timeboxing): Stage 1 runs before the Priorities question (#411)

The run loop consulted first_hard_user_blocker() before the Stage 1
branch, so every auto-started session asked 'what do you want out of the
day' before a single probe and the ladder read 1 -> 2 -> 1. The shape of
the day comes first now; the request is asked when the stage closes.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The Stage 1 judges know what time it is

**Files:**
- Modify: `src/fateforger/agents/timeboxing/elicitation_judges.py` — `_COVERAGE_PROMPT` (`:232`), `CoverageJudge.classify` (`:277`), `_PROBE_PROMPT` (`:332`), `ProbeJudge.generate` (`:356`), `elicit` (`:541`)
- Modify: `src/fateforger/slack_bot/timeboxing_host.py:408` (the `elicit` call in `_frame_from_corpus`)
- Modify: `tests/fixtures/stage1/elicit_stub.py` (`StubElicit.__call__` records `now`)
- Modify: `tests/unit/test_elicitation_judges.py:373-432` (`_StubCoverage.classify`, `_StubProbe.generate`, `_run`)
- Modify: `tests/unit/test_timeboxing_host_stage1_rows.py`
- Create: `tests/evals/test_stage1_clock.py`

**Interfaces:**
- Produces: `elicit(snapshot, rows, judges, *, session_key, now: datetime, concurrency=16, generate_for=3)` — `now` required, tz-aware. `CoverageJudge.classify(..., now: datetime)`, `ProbeJudge.generate(..., now: datetime)`. Both prompts receive a `"clock"` object: `{"now": "<Weekday YYYY-MM-DD HH:MM> <tz>", "planning_day": "<Weekday YYYY-MM-DD>", "days_until_planning_day": <int>}` — the integer is date arithmetic.
- Consumes: `HostPlanningContext._now()` (injected clock), `PlanningDay.timezone`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_elicitation_judges.py`, extend the stubs to record the clock and make `_run` pass one:

```python
NOW = datetime(2026, 9, 8, 10, 9, tzinfo=ZoneInfo("Europe/Amsterdam"))

class _StubCoverage:
    def __init__(self, table: dict[str, str]) -> None:
        self.table = table
        self.asked: list[str] = []
        self.clocks: list[datetime] = []

    async def classify(self, *, cell, rules, stated, request, session_key, now):  # noqa: ANN001
        self.asked.append(cell.id)
        self.clocks.append(now)
        return self.table.get(cell.id, "covered"), "stub"

class _StubProbe:
    def __init__(self, grounded: set[str]) -> None:
        self.grounded = grounded
        self.asked: list[str] = []
        self.seen: list[tuple[str, list[str], list[str]]] = []
        self.clocks: list[datetime] = []

    async def generate(self, *, cell, rules_full, conversation, request, session_key, now):  # noqa: ANN001
        from fateforger.agents.timeboxing.session_contracts import ProbeDraft
        self.asked.append(cell.id)
        self.clocks.append(now)
        self.seen.append((cell.id, list(conversation), [str(r["uid"]) for r in rules_full]))
        if cell.id not in self.grounded:
            return None
        return ProbeDraft(cell_id=cell.id, question=f"about {cell.id}?", why_needed="stub")

def _run(snapshot, judges, rows=ROWS_FIXTURE, now=NOW) -> ElicitationResult:
    return asyncio.run(elicit(snapshot, rows, judges, session_key="C1:1.0", now=now))
```

Then the tests:

```python
def test_both_judges_are_handed_the_clock_elicit_was_given() -> None:
    """'in 2 hours' at 10:09 became 'by 9:30 AM' because the judges knew the
    day and not the time (#412). Every classify and every generate sees the
    same tz-aware moment."""
    judges = _judges({"elicit.body.unclear": "uncovered"}, grounded={"elicit.body.unclear"})
    _run(_snapshot(), judges)
    assert judges.coverage.clocks and all(c == NOW for c in judges.coverage.clocks)
    assert judges.probe.clocks == [NOW]


def test_a_naive_clock_is_refused_before_any_judge_runs() -> None:
    """A clock with no zone cannot be placed against a planning day in
    Europe/Amsterdam; failing loudly beats a probe about the wrong hour."""
    judges = _judges({"elicit.body.unclear": "uncovered"})
    with pytest.raises(ValueError, match="tz-aware"):
        _run(_snapshot(), judges, now=datetime(2026, 9, 8, 10, 9))
    assert judges.coverage.asked == []
```

In `tests/unit/test_timeboxing_host_stage1_rows.py` (the shared stub records `now` after this task):

```python
def test_the_host_hands_elicit_the_clock_in_the_planning_timezone(stub_elicit) -> None:
    """The bot's clock is UTC; the day is planned in Europe/Amsterdam. 08:09Z
    is 10:09 there, and that is the hour the judges must reason from."""
    runtime = SimpleNamespace(
        timeboxing_constraint_store=_Store(),
        timeboxing_intent_model_client=object(),
        timeboxing_judge_model_client=object(),
    )
    utc_now = datetime(2026, 9, 8, 8, 9, tzinfo=timezone.utc)
    host = HostPlanningContext(runtime, now=lambda: utc_now)
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00"}, source="user")

    asyncio.run(host.resolve(_snapshot(frame), target=ArtifactKind.SKELETON, progress=_Sink()))

    [(_, _, now)] = stub_elicit.calls
    assert now.tzinfo is not None and str(now.tzinfo) == "Europe/Amsterdam"
    assert (now.hour, now.minute) == (10, 9)
    assert now == utc_now
```

`StubElicit.calls` becomes a list of `(snapshot, rows, now)` triples; update the two other tests that unpack it (`test_timeboxing_host_frame_from_corpus.py`, the integration route test) to the new arity.

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_elicitation_judges.py tests/unit/test_timeboxing_host_stage1_rows.py -v -p no:randomly`
Expected: FAIL — `elicit() got an unexpected keyword argument 'now'`, and the host test fails to unpack a third element.

- [ ] **Step 3: Implement**

`elicit` gains the required kwarg and refuses a naive value before touching a judge; the value is threaded into every `classify` and `generate`:

```python
async def elicit(
    snapshot: PlanningSessionSnapshot,
    rows: list[dict[str, Any]],
    judges: Judges,
    *,
    session_key: str,
    now: datetime,
    concurrency: int = 16,
    generate_for: int = 3,
) -> ElicitationResult:
    ...
    if now.tzinfo is None or now.utcoffset() is None:
        # A naive clock cannot be placed against a planning day in a named
        # zone; a probe about the wrong hour is worse than no probe (#412).
        raise ValueError("elicit needs a tz-aware `now` in the planning timezone")
```

Pass `now=now` in the `judges.coverage.classify(...)` call inside `_one` and in the `judges.probe.generate(...)` call. Both judges add `now: datetime` to their keyword-only signature and put one object in their JSON prompt, after `"request"`:

```python
def _clock(now: datetime, planning_day: date) -> dict[str, Any]:
    """What the judges are told about time. Weekday and zone are spelled out
    because a model reads "Tuesday 10:09 Europe/Amsterdam" more reliably than
    an ISO string; the day count is date arithmetic, not a judgement."""
    return {
        "now": f"{now:%A %Y-%m-%d %H:%M} {now.tzinfo}",
        "planning_day": f"{planning_day:%A %Y-%m-%d}",
        "days_until_planning_day": (planning_day - now.date()).days,
    }
```

`classify` and `generate` need the planning day: pass `planning_day: date` as a second new kwarg from `elicit` (it already holds `day = snapshot.planning_day.date`), and put `"clock": _clock(now, planning_day)` in both prompts.

Prompt text. Append to `_COVERAGE_PROMPT`, before "Return only the requested schema.":

> You are told the current time and the day being planned. When the planning day is today, an arrival or start the person named that is already behind the clock is not an open question; when it is a later day, the clock only tells you how far off it is.

Append to `_PROBE_PROMPT`, before "If nothing the user has said grounds a question":

> You are told the current time and the day being planned. Anything the person said relative to now — "in 2 hours", "after lunch", "before I leave" — resolves against that clock. Never ask about a moment that has already passed on the planning day, and name times as clock times the person can check.

Host, in `_frame_from_corpus`, beside the existing `elicit` call:

```python
        result = await elicit(
            seen,
            constraints,
            build_judges(model_client),
            session_key=snapshot.session_key,
            # The bot's clock is UTC; the day is planned in its own zone, and
            # "in 2 hours" means two hours from the local time (#412).
            now=self._now().astimezone(ZoneInfo(planning_day.timezone)),
        )
```

`StubElicit.__call__` gains `now: datetime` as a keyword-only parameter and records `(snapshot, rows, now)`.

- [ ] **Step 4: Run to verify they pass, then the package suite**

Run the two files, then `$PY -m pytest tests -q -m "not slow" -p no:randomly`. Expected: 0 failed. Any caller of `elicit`, `classify` or `generate` without `now` fails loudly at this point and is fixed by passing the clock, never by defaulting it.

- [ ] **Step 5: The eval, resampled**

Create `tests/evals/test_stage1_clock.py`, marked `slow`, following the client-and-judges setup in `tests/evals/test_stage1_elicitation.py` (real judges on the flash pin; the pro pin as the non-contender judge). Fixture: a Tuesday 2026-09-08 working day, the frozen store's rows, a `DAY_FRAME` fact of 07:00–23:00, one `ELICITED_STATEMENT` of *"im going to my own office in 2 hours, so thats a 30 minute commute."*, and `now = datetime(2026, 9, 8, 10, 9, tzinfo=ZoneInfo("Europe/Amsterdam"))`. Run `elicit` **n = 8** times. For each run, take the probe on the `movement` or `fixed` row if one was drafted, and ask the non-contender judge one schema-bound question about its text: *does this question refer to a clock time earlier than 10:09 on the planning day?* Assert that at most 1 of 8 does. Record the rate in the test's docstring after the first run, and keep the raw questions in the report.

Run: `$PY -m pytest tests/evals/test_stage1_clock.py -v -m slow -p no:randomly`, with the parent `.env` loaded the way `test_stage1_elicitation.py` loads it.

- [ ] **Step 6: Break it on purpose**

Remove the `"clock"` key from the probe prompt. Confirm `test_both_judges_are_handed_the_clock_elicit_was_given` still passes (it tests plumbing, not the prompt) and that the eval's rate collapses. Remove the naive-clock check; confirm `test_a_naive_clock_is_refused_before_any_judge_runs` fails. Restore both.

- [ ] **Step 7: Commit**

```bash
git add src/ tests/
git commit -m "feat(timeboxing): the Stage 1 judges are told what time it is (#412)

'in 2 hours' at 10:09 came back as 'by 9:30 AM' because the judges knew
the day being planned and not the time of day the conversation was
happening. elicit takes a required tz-aware clock; the host derives it
from its injectable clock in the planning timezone; both prompts say what
to do with it. A naive clock is refused before any judge runs.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The gate line names each open row once

**Files:**
- Modify: `src/fateforger/slack_bot/stage_cards.py:408-425` (`GATE_LINE_CAP`, `_gate_line`)
- Modify: `tests/unit/test_stage_cards.py:528-536` and `:581-606`

**Interfaces:**
- Consumes: `Gate.open_cells: list[CellRef]`, `ROWS`, `CRITERIA`, `row_label`, `criterion_label` from `elicitation`.
- Produces: `_gate_line(gate) -> str` of the form `Still need: <row> (<criterion>, <criterion>) · <row> (<criterion>).` Rows in `ROWS` order, criteria in `CRITERIA` order.

- [ ] **Step 1: Write the failing tests**

Rewrite the two existing assertions and add one:

```python
def test_a_probe_card_names_what_is_still_needed_and_offers_no_next() -> None:
    gate = Gate(open_cells=[CellRef(row="body", criterion="unclear")], day_label="working Tuesday")
    pending = PendingBlocker(requirement_id="elicit.body.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    outcome = AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="body", gate=gate)
    card = _map(outcome, _snapshot(pending_blocker=pending))
    assert card.stage.index == 1
    assert card.gate == "Still need: body (clarity)."
    assert not any(isinstance(c, NextControl) for c in card.controls)


def test_the_gate_line_groups_open_cells_by_row() -> None:
    """Four rows open on 'assumptions' used to render as 'assumptions' four
    times in a flat list, as though it were four separate needs (#413)."""
    from fateforger.slack_bot.stage_cards import _gate_line

    gate = Gate(
        open_cells=[
            CellRef(row="movement", criterion="alternatives"),
            CellRef(row="fixed", criterion="tacit_assumptions"),
            CellRef(row="movement", criterion="tacit_assumptions"),
            CellRef(row="body", criterion="unclear"),
        ],
        day_label="working Tuesday",
    )
    line = _gate_line(gate)
    assert line == (
        "Still need: what is fixed (assumptions) · "
        "movement and transitions (assumptions, alternatives) · "
        "body (clarity)."
    )


def test_the_gate_line_for_every_cell_names_each_row_once_and_fits_a_section() -> None:
    """Turn one is when the most cells are open. All 45 grouped come to a few
    hundred characters; nothing is capped or sliced on the way out."""
    from fateforger.agents.timeboxing.elicitation import ALL_CELLS, ROWS
    from fateforger.slack_bot.messages import SLACK_MAX_BLOCK_TEXT_CHARS
    from fateforger.slack_bot.stage_cards import _gate_line
    from fateforger.slack_bot.timeboxing_cards import render_stage_card

    gate = Gate(open_cells=list(ALL_CELLS), day_label="working Tuesday")
    line = _gate_line(gate)
    for row in ROWS.values():
        assert line.count(f"{row.label} (") == 1
    assert "more_" not in line
    assert len(line) < SLACK_MAX_BLOCK_TEXT_CHARS

    card = _map(GateMet(gate=gate), _snapshot())
    assert card.gate == line
    sections = [b["text"]["text"] for b in render_stage_card(card).blocks if b.get("type") == "section"]
    assert line in sections
```

Delete `test_the_gate_line_caps_the_open_cells_and_names_the_overflow`; the third test above replaces it.

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/unit/test_stage_cards.py -v -p no:randomly -k gate`
Expected: FAIL on the exact-string assertions.

- [ ] **Step 3: Implement**

```python
def _gate_line(gate: Gate) -> str:
    """What Stage 1 still needs, one clause per open row.

    Grouped by row rather than listed per cell: four rows open on the same
    criterion used to read as that criterion named four times, as if it were
    four separate needs (#413). Row and criterion keys are identifiers this
    system minted, so grouping and ordering them is arithmetic. Eight rows is
    the whole floor, so nothing is capped.
    """
    if not gate.open_cells:
        return (
            f"That's what I know to ask about a {gate.day_label}. "
            "Anything else, or shall I plan?"
        )
    open_by_row: dict[str, set[str]] = {}
    for cell in gate.open_cells:
        open_by_row.setdefault(cell.row, set()).add(cell.criterion)
    clauses = [
        f"{row_label(row)} ("
        + ", ".join(criterion_label(c.key) for c in CRITERIA if c.key in open_by_row[row])
        + ")"
        for row in ROWS
        if row in open_by_row
    ]
    return "Still need: " + " · ".join(clauses) + "."
```

Import `CRITERIA` and `ROWS` from `elicitation` beside the existing `row_label`/`criterion_label` imports. Delete `GATE_LINE_CAP` and remove it from `__all__` if listed.

- [ ] **Step 4: Run to verify they pass, then the package suite**

Run the file, then the package suite. Expected: 0 failed.

- [ ] **Step 5: Break it on purpose**

Change the between-clause separator back to `", "`. Confirm `test_the_gate_line_groups_open_cells_by_row` fails. Restore.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/slack_bot/stage_cards.py tests/unit/test_stage_cards.py
git commit -m "fix(slack): the gate line names each open row once (#413)

Row and criterion were joined with the same comma, so four rows open on
'assumptions' read as 'assumptions' four times over. Grouped by row.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The whole suite, a live Stage 1, and the draft PR

**Files:** none in `src/` unless a failure demands it.

- [ ] **Step 1: The package suite.** `$PY -m pytest tests -q -m "not slow" -p no:randomly`. Expected: 0 failed.

- [ ] **Step 2: Drive one Stage 1 turn against the real judges and look at the line.** Follow `tests/evals/test_stage1_elicitation.py`'s setup to run `elicit` once on the frozen store for a working Tuesday with the *"in 2 hours"* statement at 10:09 local, then render the resulting `GateMet`/`AwaitingUser` through `map_outcome` and `render_stage_card` and post the blocks as the bot to `#ff-e2e` (`C0BRMUFU2VD`, thread `1788780130.551149`) using `SLACK_BOT_TOKEN` from the parent `.env` via `chat.postMessage`. Read it back and confirm in the report: the gate line groups by row, the probe names a time after 10:09, and the message is whole.

- [ ] **Step 3: Rebase.** `git fetch origin && git rebase origin/feat/stage-card-whole`, re-run the suite. If #397 has merged by now, rebase onto `origin/main` instead and say so.

- [ ] **Step 4: Open the draft PR.** `git push -u origin feat/stage1-first`, then `gh pr create --draft --base main` with title *"Stage 1 first: the shape of the day, a clock for the judges, and a readable gate line (#411, #412, #413)"*. The body carries the three tickets, the ladder before/after, the eval rate from Task 2, and a human checklist: start a session and confirm the first card is a Stage 1 probe; say "in 2 hours" and confirm the probe's time is after now; confirm the gate line reads as rows. State whether it is blocked by #397. End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 5: Do not comment on the tickets.** The controller does that after the whole-branch review.
