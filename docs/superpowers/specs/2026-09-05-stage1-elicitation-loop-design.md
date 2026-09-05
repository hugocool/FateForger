# Stage 1 elicitation, increment B: the loop that fills the matrix

**Status:** design, approved by Hugo 2026-09-05. **Amends**
`2026-09-04-stage1-elicitation-design.md`; it does not restate it. Everything that
design settled — the layered spec, the fourteen criteria, the matrix and the arithmetic
gate, the decision set, steering, consent as the next message — stands. This document
records only what that design left open or what has changed since Phase 1 merged (#295,
#301). Governs #262. Absorbs #284, closes #285 and #286, re-purposes #283, does #290.

## The defect this increment closes

Phase 1 built every part of Stage 1 except the part that fills it. Nothing in `src/`
writes a `COVERAGE_MATRIX` fact — `elicitation.py` only reads one — and when the matrix
is absent `stage1_gate` returns `open_cells=[]`. So the live Stage 1 locks the day,
shows the rules, and **proposes to close on the same turn**. It asks nothing. That is
#262's original symptom with a consent step in front of it.

The parent design's plan section already says where the missing part lives: *"the
judgements run in the host's `resolve`, the way `DayFrameJudge` already does … the kernel
stays arithmetic and gains no model port."* Steps 2 (place), 3 (classify) and 5
(generate) of that plan are unbuilt; steps 1, 4, 6, 7, 8 are merged. This increment
builds the three and changes nothing about the eight.

## Decisions

| fork | choice | why |
| --- | --- | --- |
| the spike series (#283–#286) | **B forfeited; A′ built as the design's own plan; C proven by stubs** | Phase 1 put the matrix in the snapshot and the gate in the kernel — B's premise ("the kernel holds no matrix") is gone unless shipped code is torn out. A′'s kernel half, including the offline gate test that was its whole argument, is merged. What remained open was only where classify and the words run, and the parent design's plan section answers it. |
| module structure | **three judges + one orchestrator** | C's composability proof becomes two stub fixtures instead of a partial stub of one object; the seam is a type, not a convention. |
| the measuring instrument | **ablation floor + turns-to-`GateMet`; hand-labels retired** | A gold set of "gaps a good coach would ask" can never be complete, so recall against it punishes a correct probe; the measured failure mode is 23/35 cells uncovered (the gate never opening), which labels cannot see; and the annotator is the corpus's author. Ablation constructs the gap, so recall is exact; turns-to-gate is what a person feels. Hugo, 2026-09-05, after a blindspot pass. |
| the seventh concern | **`method`: how the day gets planned** | The six durable rules about planning itself (*Block exit criteria*, *Deep-work entry criteria gate*, *Artifact-first scheduling gate*, *C2F framing cap*, *No morning meetings*, *Revenue/outreach duration cap*) fit no concern and carry no anchor. A row keeps contradiction detection live (*No morning meetings* against a fixed 09:00) and keeps `unplaced` meaning a placement failure. |
| generate for how many cells | **top three, in parallel** | One round-trip either way; a cell the model cannot ground falls through to the next instead of costing the user a turn. |
| an ungroundable cell | **recorded in `unaskable`, state stays `uncovered`** | `ranked_open_cells` opens a cell iff its state is `uncovered`; `unaskable` only sorts it last. Flipping the state would open the gate on a question nobody asked. |
| the simulated user's model | **a non-contender, pinned separately** | If it shares the classify/generate lineage, turns-to-gate measures two copies of one judgement agreeing. Same rule the parent design applies to the framing judge. |
| the feedback transport (#293) | **out of scope** | The RDR-shaped learning loop is the right steady-state source of labelled cases and it is its own increment. |
| where the work happens | **a worktree, one PR rebased on main** | Hugo's rule; and the shared checkout carries another session's uncommitted edits. |

## 1. The missing middle

### 1.1 Three judges

New module `src/fateforger/agents/timeboxing/elicitation_judges.py`. Each judge follows
`DayFrameJudge` exactly: the constructor takes a `ChatCompletionClient`; one async
method; the prompt is `json.dumps` of a dict with `sort_keys=True`; the call runs under
`llm_attribution(agent="timeboxing_agent", call_label=..., key=session_key)` with
`json_output=` a strict Pydantic schema; content that is not a string raises. No judge
reads or writes the snapshot.

**`PlacementJudge.place(*, anchors, unanchored_rules, session_key) -> Placement`**

One batched call. Input: every anchor present in the day's active set with two example
rule names each, *and* every active rule that carries no anchor, by name and description.
Output: `Placement(anchors: dict[uid, row_key], rules: dict[uid, row_key])`, each value a
`Literal` over the concern keys plus `unplaced`. The schema is the seven concerns plus
`unplaced`; `request` is never a placement target. Measured at n=5 on the live 29 anchors:
27 unanimous, none outside the schema (findings §1).

**`CoverageJudge.classify(*, cell, row, rules, stated, request, session_key) -> CellState`**

One narrow call per cell. Input: the row's label and description, the criterion's
question, the row's rules **by name and necessity only**, the stated facts and elicited
statements this session, and the request. Output: `Literal["covered", "uncovered",
"not_applicable"]` plus a `why` of at most fifteen words, kept for the eval report and
never rendered.

**`ProbeJudge.generate(*, cell, row, rules_full, conversation, session_key) -> ProbeDraft | None`**

Input: the cell, the row's rules with **full descriptions**, everything the user has said
this session, the nine framing criteria as instructions, and the grounding clause *"based
only on what the user has said"*. Output: `ProbeDraft(cell_id, question, why_needed,
options: list[BlockerOption] ≤ 4)` or nothing. Options only when the answer set is closed.
*"A no-op is a perfectly good outcome; do not invent a question to justify the run."*

### 1.2 The orchestrator

`elicit(snapshot, rows, judges, *, session_key) -> ElicitationResult` in the same
module, where `judges` is a small dataclass holding the three. `ElicitationResult` is
`(matrix_fact: PlanningFact, probes: list[ProbeDraft])`.

1. **Place.** Read the previous matrix from the snapshot, if any. If the set of uids in
   `rows` (anchor uids and rule uids, set equality over minted ids) equals the set the
   cached placement was made against, reuse it. Otherwise call `PlacementJudge` once.
2. **Applicability, arithmetic.** For every row, `RowStats(rule_count, must_count,
   stated)` from the placement and the snapshot's facts. A cell whose row has zero rules
   and zero stated facts is `not_applicable` **without a call**. The `request` row is
   `not_applicable` until a `REQUESTED_ACTIVITY` fact exists.
3. **Classify.** Every cell not already `covered` in the previous matrix and not
   `not_applicable` by step 2 goes to `CoverageJudge` in one bounded-concurrency batch
   (`asyncio.Semaphore`, default 16). The whole batch completes before anything is
   assembled. A single failure fails the turn.
4. **Rank.** `ranked_open_cells(matrix, assumed)` — the existing arithmetic, with
   `assumed` the cell ids of the snapshot's assumptions.
5. **Generate.** `ProbeJudge` for the first three ranked cells, in parallel. A cell whose
   draft is `None` is appended to `matrix.unaskable`; its state stays `uncovered`.
6. **Return.** The matrix as a `PlanningFact(kind=COVERAGE_MATRIX, fact_id=coverage_fact_id(day), source="system")`,
   rewritten whole, and the probes that grounded, in rank order.

Two round-trips per turn at p50 (classify batch, then generate), placement a third only
when the active set changed. 45 classify calls at ~1 s p50 each, concurrent.

`CoverageMatrix` gains `rule_placement: dict[str, str]` beside `placement`, for the
unanchored rules, and `placed_against: list[str]` — the sorted uids the placement was
made against, which is what step 1 compares.

### 1.3 Host wiring

`TimeboxingHost._frame_from_corpus` runs the frame judgement as today, then `elicit`,
and returns `PlanningContext(facts=[frame?, matrix_fact], applicable_constraints=rows,
suspended_constraint_count=..., probes=probes)`. `PlanningContext` gains
`probes: list[ProbeDraft] = []`. The judges are built from
`runtime.timeboxing_intent_model_client`; no client is `AdaptiveDependencyUnavailable`,
as for the frame judge.

The stated facts and conversation the judges see are read from the snapshot the kernel
handed to `resolve`: `DAY_FRAME`, `REQUESTED_ACTIVITY`, `ELICITED_STATEMENT`,
`SUSPENDED_CONSTRAINT` facts, and the assumptions. Rows already suspended this session
are dropped from `rows` before placement, the way the brief already drops them.

### 1.4 The kernel touch

`_stage1_outcome` prefers the resolved probe whose `cell_id` equals the top open cell's
id; otherwise it asks with the catalog's criterion text as today. `question`,
`why_needed` and `options` come from the draft. `PendingBlocker` binds the answer to the
cell as before. `stage1_gate`, `GateMet`, the consent transition and the decision set are
untouched. The run loop carries `resolved.probes` from `resolve` to `_stage1_outcome`
and nowhere else; probes never reach the snapshot.

## 2. The seventh concern

`CONCERNS` gains, last:

```
Concern("method", "how the day gets planned",
        "rules about the planning itself: gates, caps, orderings; not about a thing in the day")
```

`ALL_CELLS`, the `CoverageMatrix` completeness validator, `ROWS`, `row_label` and the
catalog's cell requirements all derive from the tuple; 40 becomes 45 with no other edit.
Every existing row key and criterion key is unchanged — `stage_context` tests and the
panel walk use `elicit.body.unclear` and `elicit.fixed.unclear` as literals.

## 3. The fixture

### 3.1 Relink (#290)

`scripts/memory/relink_anchors.py`, importing the split path's relink from
`reprojection.py` (around 420–432: read `observation.anchors`, `resolve_anchors`,
`replace_constraint_links`). `--dry-run` prints, per unanchored durable rule, the names
its observations carry and the uids they resolve to; `--apply` writes links only — no
observation, no projection. Expected: 8 relinked, 6 unresolved; the six are the `method`
rules and their not resolving is asserted, not reported as failure. Run on a copy, then
on `data/memory.db` after `cp data/memory.db data/memory.db.bak-<date>-pre-relink`.

### 3.2 Freeze

After the relink, `cp data/memory.db data/fixtures/stage1-<date>.db` (gitignored, as
every copy of the corpus is). Its sha256 is `FIXTURE_STORE_SHA256` in
`tests/fixtures/stage1/days.py`; `rows_for` refuses a store whose hash differs. The eval
reads `STAGE1_FIXTURE_DB` and skips without it, as today. Re-freezing is a deliberate
act: bump the hash, re-run the evals.

The memory schema is a versioned ladder that refuses a database it does not understand.
`admonish-1-76` owns a v5 migration of the live store and will say before it runs; the
freeze happens after it if it lands first, and is redone if it lands after.

`labels.toml`, `load_labels`, `LabelledGap` and
`test_every_day_has_hand_labels_before_a_spike_may_run` are deleted.

## 4. The evals

`tests/evals/test_stage1_elicitation.py`, marked slow, real model through OpenRouter,
n=5 draws, rates asserted, never a single draw. Contender judges run on the extraction
model and effort in CLAUDE.md. The simulated user and the framing judge are pinned to a
different model named in one place, `NON_CONTENDER_MODEL`.

### 4.1 Golden days

`tests/fixtures/stage1/golden.toml`: per fixture day, the facts a good coach would end
up with — deep-work start and length, gym slot, dinner, commute, sleep — eight lines for
the working Tuesday, three each for the vacation day and the Sunday. Drafted from the
frozen store's rules and **corrected by Hugo before the first eval run**. This is the
simulated user's script, not ground truth for any judgement.

### 4.2 The simulated user

`SimulatedUser.answer(probe, golden) -> Reply(kind: Literal["answered", "not_relevant",
"already_said"], text)`. A non-contender model playing Hugo, answering only from the
golden facts and the replies it already gave. Its `kind` is the instrument.

### 4.3 The loop under test

`run_stage1(day, judges, user) -> Trace`: build the fixture snapshot, call `elicit`, fold
the reply as an `ELICITED_STATEMENT` for the asked cell (the kernel's fold, not a copy),
repeat until `stage1_gate` has no open cells or a cap of twelve turns. The trace records
every probe, reply kind, matrix and round-trip count.

### 4.4 Measures and thresholds

| measure | assertion at n=5 |
| --- | --- |
| turns to `GateMet` | working Tuesday ≤ 4 at p50; every day reaches it within the cap in ≥ 4/5 draws |
| nuisance rate | `not_relevant` replies ≤ 1 in 5 probes, pooled |
| re-ask rate | `already_said` replies = 0 |
| round-trips per probe | ≤ 2 at p50, counted from the judges' call log |

Thresholds are Hugo's to move before the first run and not after (#283's rule).

### 4.5 The ablation floor

Each case is `(transform of the golden Tuesday, expected_cell)`:

| transform | expected cell |
| --- | --- |
| strip `18:00` from the request | `request.tacit_knowledge` |
| drop the deep-work duration from golden | the `tacit_knowledge` cell of the row deep work is placed under |
| add a fixed 09:00 meeting to the stated facts | `method.contradictory` (*No morning meetings*) |
| remove every dinner row from `rows` | the dinner row's cells become `not_applicable`, never `uncovered` |

Assertion one is arithmetic: the expected cell is `uncovered` in ≥ 4/5 draws and is the
first cell asked. Assertion two is the framing judge: the probe text addresses the
removed fact, ≥ 4/5.

## 5. The composability proof (closes #286)

Unit tests, no model, in `tests/unit/test_elicitation_composes.py`:

1. **Stub `ProbeJudge`** to one fixed string; `CoverageJudge` stubbed from a table. Run
   `elicit` on all three fixture days → `stage1_gate` decides identically to the same
   table without the probe stub. The gate does not depend on the words.
2. **Stub `CoverageJudge`** to mark one fixed cell `uncovered`; real ranking; a stubbed
   `ProbeJudge` returning a draft. The probe renders on the card and a typed reply binds
   through `SurfaceIntentInterpreter` to that cell. The voice does not depend on the spec.
3. **Stub `PlacementJudge`** to put everything in `unplaced` → the gate still opens after
   the cells are covered; nothing is dropped.
4. **The seam from the card's side** (per `admonish-1-52`): a `CoverageMatrix` with an
   `elicit.method.*` cell uncovered and a rule placed under `method` ranks that row as
   open in `stage_context`.

## 6. Prep and tickets

- `demo.py restart` after merge: the memory server and the bot are serving code from
  before both Stage 1 merges.
- `tests/unit/test_planning_reminder_suppression.py::_session` derives `day_type` from
  the weekday so it stops failing on weekends; `tests/e2e/test_slack_handoff_flow.py`
  asserts the current post format (#281 removed the `*agent*\ntext` wrapper it expects).
- **#294** in passing: `map_outcome` takes the catalog from the kernel, since this
  increment changes that catalog.
- Tickets: #297 close (fixed by 932ddfe); #276 close (the catalog carries `stage`);
  #262 "Phase 1 landed, loop in this PR"; #285 close as overtaken; #284 and #286 close
  with the PR; #283 re-titled to the eval it now names; #290 close with the relink.
  File: "timeboxing agent addressed in a thread with no open session starts one"
  (entry policy, from `admonish-1-34`'s routing fix); post the number to them.

## 7. Testing and errors

**Unit** (`tests/unit/test_elicitation_judges.py`, judges stubbed, the `DayFrameJudge`
tests are the template): placement cache reused iff `placed_against` matches; step-2
`not_applicable` costs no call; only non-covered cells are classified; the batch is
atomic — one failing call fails the turn and writes nothing; top-three fallthrough and
`unaskable` bookkeeping with state left `uncovered`; the matrix fact is rewritten whole
at the stable id; `_stage1_outcome` prefers the resolved probe and falls back to the
catalog text; `PlanningContext.probes` never reaches the snapshot (extend the AST guard
that already keeps the surfaces snapshot-only).

**Errors stay loud.** Non-schema content raises. A missing model client is
`AdaptiveDependencyUnavailable` → `TurnFailed(dependency_unavailable)` with the previous
card kept, exactly as the frame judge fails today. There is no fallback that marks a cell
covered.

## Out of scope

`steer_always` and the ask-first promotion flow; the feedback transport (#293);
near-duplicate anchors (#291); the #288 guardrails; the card grammar (#266, merged).

## Files

| file | change |
| --- | --- |
| `src/fateforger/agents/timeboxing/elicitation_judges.py` | new: three judges, `Judges`, `ProbeDraft`, `Placement`, `elicit` |
| `src/fateforger/agents/timeboxing/elicitation.py` | `method` concern; `rule_placement`, `placed_against` on the matrix |
| `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | `PlanningContext.probes`; `_stage1_outcome` prefers the resolved probe |
| `src/fateforger/slack_bot/timeboxing_host.py` | `_frame_from_corpus` runs `elicit` |
| `src/fateforger/slack_bot/stage_cards.py` | #294: catalog from the kernel |
| `scripts/memory/relink_anchors.py` | new: #290 dry-run / apply |
| `tests/fixtures/stage1/days.py`, `golden.toml` | hash pin; golden days; labels removed |
| `tests/evals/test_stage1_elicitation.py` | simulated user, measures, ablation |
| `tests/unit/test_elicitation_judges.py`, `test_elicitation_composes.py` | unit and composability |
| `tests/unit/test_planning_reminder_suppression.py`, `tests/e2e/test_slack_handoff_flow.py` | the two stale tests |
