# Stage 1 as elicitation: collecting the constraints that hold today

**Status:** design, approved by Hugo 2026-09-04 after a blindspot pass and four spikes
(`docs/superpowers/research/2026-09-04-stage1-spike-findings.md`). Governs #262. Feeds
#266. Loop location is spiked in #283 to #286 and left as a parameter here.

## The defect

Stage 1 ("Constraints") completes the moment the day type is confirmed, with a ✅ and
nothing shown. The requirement catalog has no user-owned requirement in Stage 1 except
`skeleton.day_frame`, which memory satisfies silently. The calendar and constraint reads
are system-owned and satisfy themselves. Worse than the spec first said: **Stage 1
receives no constraints at all.** `TimeboxingHost.resolve(target=SKELETON)` fetches the
active rules only to derive the day frame and returns them to nobody; they reach the
planner's brief two stages later, at `VALIDATED_CANDIDATE`. The user sees a count that
is never rendered, and the session root reads *"No active constraints yet"* because it
queries a legacy store nothing writes.

The legacy agent's Stage 1, *gather fixed events, commutes, arrivals, habits scope, energy
profile, sleep target; confirm "LOCKED?" before moving on*, was the planner showing the
user what it knew and asking them to correct it before planning. That is what is gone.

Hugo's requirement adds one thing legacy never had: the *kinds* of constraint must not
be a list in a prompt. A new kind he states should appear in Stage 1 without anyone
editing anything.

## Decisions

Each was a fork in the brainstorm. The choice and the reason, so nobody re-derives them.

| fork | choice | why |
| --- | --- | --- |
| what Stage 1 does | **show, and fill gaps dynamically** | show-only never notices what is missing; a checklist of what is missing is the disease one level up |
| where the spec comes from | **layered: authored concern-floor + anchors from memory** | memory records what was said, never what was not; the floor is the only way to ask about something never mentioned |
| how the layers meet | **one batched placement call per session, cached in the matrix fact** | measured 27/29 anchors unanimous at n=5; nothing else assigns an anchor to a concern, and without it the layers never touch |
| what the rows are | **concerns, plus `unplaced`, plus `request`; anchors are headings** | per-anchor rows left 59 of 115 cells open on the fixture day, a gate that cannot be met; the gap that repeated eleven times lived in the request, which had no row |
| when Stage 1 ends | **the user ends it; the agent proposes** | the whole point of eliciting is that the other party does not close the door; a cap is a magic number deciding how much of the day the agent may learn |
| what consent looks like | **the next message**, never a timer | in async Slack silence means done, thinking, or gone; a timer acts while the user is away |
| how to force past an unmet gate | **a user-filed assumption** | `PlannerAssumption` already satisfies a requirement by assumption; forcing is the user supplying the missing fact as one, visible and reversible |
| how the criteria run | **classify, then generate** | the paper's path and the one that beat human interviewers; the classify batch *is* the gap detector, and it is parallel |
| what a reply is, typed | **`ELICITED_STATEMENT`, observed into memory after the save** | reusing `REQUESTED_ACTIVITY` is why the planner cannot tell a request from a constraint and why answers evaporate with the session |
| how "not today" binds | **a snapshot fact binds; an observation records** | a suspension carried by memory depends on a projection and vanishes in an outage; a suspension nobody records teaches the writer nothing |
| does `day_frame` stay | **yes; the `bounded` row reads its fact** | retiring it makes the frame a soft cell "assume" can wave through, which is #251 again |
| where the loop runs | **spiked** (#283 to #286): A′ host-typed, C split, B harness | not predictable by reasoning; A′ meeting thresholds forfeits B, C runs regardless |

## The spec the agent reasons against

Two layers, and one call that joins them. The first layer is authored once and is the
only hardcoded thing in this design. The second is the memory server's and grows on its
own.

### Layer 1: the concern-floor

Six concerns, at the level of *what a day has to have settled*, not what words describe
it. Drafted from the clusters in the live anchor store; **Hugo corrects this before any
spike runs** (#283).

1. **How the day is bounded**: when it starts and ends, what frames it.
2. **What is fixed**: events, appointments, arrivals that do not move.
3. **Movement and transitions**: commutes, travel, the gaps between fixed things.
4. **Body**: food, sleep, energy, exercise; the physical constraints on attention.
5. **Fragile intentions**: the things that only happen if protected; the reason a planner exists.
6. **What today is not**: suspensions, rules that usually hold and do not today.

Two rows the floor does not author sit beside these in the matrix: **`unplaced`**, for
anchors the placement call cannot put under a concern, and **`request`**, for what the
user said they want from the day this session. Neither is a concern; both are places a
gap can live that no concern covers.

The floor correction has one known input already: six durable rules in the live store
(*Block exit criteria*, *Deep-work entry criteria gate*, *Artifact-first scheduling
gate*, *C2F framing cap*, *No morning meetings*, *Revenue/outreach duration cap*) are
rules about **how the day is planned**, not about a thing in it. No concern holds them
and no anchor ever will. Whether that is a seventh concern is Hugo's call.

### Layer 2: anchors

The memory server mints an anchor for every statement at write time, by a model, once
(`memory_observe` → `resolve_anchors`). The live store holds 29, every one from Hugo's
own words. **Anchors are the categories.** A rule stated tomorrow about a thing never
mentioned before mints a new anchor and appears in Stage 1 under it. No list is edited.

`anchor_edges` is empty by design (#140): no hierarchy, so `gym` and `run` sit side by
side rather than under *exercise*. Flat is correct for a card.

Two facts about the live store shape this layer:

- **Durable rules are a third unanchored.** 42 durable rules, 14 without an anchor.
  Eight of those fourteen have observations that already name anchors (`deep work`,
  `lunch`, `reading`, `dinner`) which were never linked, because they predate the
  graph. Reprojection cannot fix it: anchors are written to the append-only log at
  ingest and `reproject` re-asks projection only (I2). The split path already relinks
  from observation anchor names (`reprojection.py:420-432`); **the same code as a
  one-off pass is the fix, and it runs before the spikes.** On the fixture Tuesday the
  unanchored group is where one of the two real conflicts came from.
- **The anchor set duplicates.** `supermarket` beside `grocery shopping`, `work` beside
  `workday`. Self-organised categories erode without pressure against near-duplicates;
  memU's one line, *"branching is preferred over spawning near-duplicates"*, belongs in
  `resolve_anchors`. Out of scope here, filed against the memory server.

**The view carries anchors.** `ConstraintView` is *"deliberately narrow: the patcher
renders these and nothing else"* and `to_view()` drops anchors. Add `anchors:
list[AnchorRef]` where `AnchorRef` is `(uid, name)`. The join happens in
`MemoryService.get_active_constraints`, which holds both stores; `read_api` keeps its
signature. The KG client (`kg_constraint_client.py`) calls `read_api` directly today
and cannot see anchors; it is repointed at the service. All of it is set membership and
lookups over uids the system minted: arithmetic, read-path safe, invariant I1 holds.

### How the layers meet: placement

Nothing assigns an anchor to a concern, and ranking, grouping and the `not_today` count
all need it. It is a judgement, so it is a model call: **one batched sampling call per
session**, given every anchor present in the day's active set with two example rule
names each, returning one concern key per anchor or `unplaced`. Measured at n=5 on the
live 29: 27 unanimous, 2 with a 4/5 or 3/5 mode, 0 outside the schema, 0 refused.
The result is cached in the matrix fact; it is recomputed only when the active set
changes. An anchor the model cannot place goes to the `unplaced` row. Nothing is
dropped.

### The fourteen criteria

From [Singhal et al.](https://github.com/anmolsinghal98/Requirements-Elicitation-Follow-Up-Question-Generation),
whose criterion-guided questions beat human interviewers.

**Coverage criteria**, five. Each is a question the agent asks *about the conversation
so far*, per row. They are content-agnostic: "have they justified their assumptions
about *sleep*?" works for an anchor that did not exist yesterday.

- tacit assumptions elicited
- alternatives considered, **only where a rule in this row is at risk given what the
  user said today**
- the unclear clarified
- the contradictory clarified
- tacit knowledge elicited

The discriminator on *alternatives* is measured, not stylistic: as the paper words it,
the criterion came back `uncovered` on every row of both spike runs, 7/7 and 23/23,
always with *"no contingency discussed"*. A criterion that can never be `covered` before
planning starts is a gate that never opens.

**Framing criteria**, nine. Each is a property of a probe's *text*. They constrain
generation and never run as a separate pass.

- not generic or domain-independent · not too long · no jargon · not technical ·
  appropriate to the person · does not ask for solutions · one kind of requirement at a
  time · not open to multiple readings · not so vague it means nothing

**Generate may return nothing.** An `uncovered` cell the generator cannot ground in what
was said yields no probe and is recorded as such; the cell stays open and is offered on
the gate line rather than asked. *"A no-op is a perfectly good outcome; do not invent a
question to justify the run."*

### Coverage matrix and gate

The matrix is `row × coverage-criterion`: eight rows (six concerns, `unplaced`,
`request`), five criteria, forty cells, each `covered | uncovered | not_applicable`. It
is a **fact in the snapshot**, `FactKind.COVERAGE_MATRIX` at the stable id
`coverage:{day}`, rewritten whole on every fold, so Back, redo and a restart all see the
same state. The fact also carries the placement (anchor → row) it was classified
against.

- A cell is `not_applicable` when its row has no applicable rule from memory *and* no
  stated fact this session. The `request` row is `not_applicable` until the user has
  said what they want from the day; Stage 1 does not ask for the request, Stage 2 does.
- **Gate met** ⇔ no cell is `uncovered`. Arithmetic. No model call decides it.
- One function, `stage1_gate(snapshot) -> Gate`, decides it for **both** the kernel's
  outcome and the interpreter's decision set. `Gate` is typed: `open_cells:
  list[CellRef]`, `day_label: str`, `note: str | None`. The card renders *"still need:
  …"* from `open_cells` using the floor's authored labels and *"that's what I know to ask
  about a working Tuesday"* from `day_label`; `note` is the only prose and may be empty.
- `GateMet` is a new kernel outcome, sibling to `AwaitingUser`, carrying a `Gate` with
  `open_cells == []`. Stage 1's `AwaitingUser` carries the same `Gate` with the cells
  still open. The renderer offers **Next** only on `GateMet`.

### Cells are catalog requirements

Every cell is a static `ArtifactRequirement`, `elicit.{row}.{criterion}`, target
`SKELETON`, owner `USER`, `hard=False`, `satisfied_by=(FactKind.ELICITED_STATEMENT,)`,
with the criterion text as its `question` and the concern's authored label as
`why_needed`. Forty requirements, generated from two fixed lists at import. Nothing in
the kernel changes to hold a cell's question: `_hold_question` finds the fact kind,
`PendingBlocker` binds the answer, `target_of` and `invalidate_from` work unchanged.
Being soft, no cell is a `first_hard_user_blocker`; the elicitor decides which cell to
ask, so the catalog's order carries no meaning.

Each `ArtifactRequirement` gains a **`stage: int`**. The card takes the stage from the
requirement behind the blocker, and `_QUESTION_STAGE`, the hand-authored map from fact
kind to stage, is deleted. `skeleton.day_frame` and every `elicit.*` cell are stage 1;
`skeleton.requested_activity` and `skeleton.activity_reading` are stage 2. That closes
#276: the ladder is 1, 1, …, 2, 3 by construction.

`skeleton.day_frame` stays user-owned and hard. The `bounded` row reads the `DAY_FRAME`
fact as a stated fact. When memory supplied the frame silently, which is the defect in
the screenshot, the row's *tacit assumptions* cell comes back `uncovered` and the probe
is *"I have you up at 07:00 and asleep by 23:30 from what you told me before, still
right for Tuesday?"*: confirmed without being re-asked as an open question.

## The plan the agent follows

One iteration of the loop, per turn. The judgements run **in the host's `resolve`**,
the way `DayFrameJudge` already does, on `runtime.timeboxing_intent_model_client`; the
kernel stays arithmetic and gains no model port.

1. **Read.** `plan_read` for the locked day; `memory_get_active_constraints(day,
   day_type)` with anchors; `memory_get_session_constraints(session_id)` for what this
   thread already established. All three are independent and run concurrently. The
   host writes the active rules to the snapshot field `applicable_constraints` on
   **every** resolve, not only at day lock: the read is model-free and the set can
   change mid-session (a promotion, a day-type change). The existing
   `ACTIVE_CONSTRAINTS` fact stays count-only; the payload lives once.
2. **Place.** If the active set changed since the cached placement, one batched call
   assigns anchors to rows.
3. **Classify.** For every cell not already `covered`, one narrow typed call: given the
   rules under this row **by name and necessity**, and what the user said this session,
   is this criterion met? Structured output, a `Literal` per cell. **All cells in one
   bounded-concurrency batch** (CLAUDE.md), 40 calls at ~1 s p50. Full rule
   descriptions go only to the generate call; classify on names halves the tokens.
   **The whole batch completes before the snapshot is touched.** A turn is atomic.
4. **Rank.** Uncovered cells ordered by expected value: a row with applicable rules or
   stated facts before one with neither, then a row carrying a `must` before one
   carrying only `should`s, then the criterion order above. Every term is a count over
   minted fields; nothing in the floor is marked "hard" by hand.
5. **Generate.** One probe for the top cell, with the nine framing criteria in the
   prompt, the full descriptions of that row's rules, and the grounding clause **"based
   only on what the user has said"**. Options are offered as buttons only when the
   answer set is closed (≤ 4). Nothing is a legal answer.
6. **Ask.** `AwaitingUser(requirement_id=cell, question, why_needed, options, gate)`.
   `PendingBlocker` binds the answer to the question that was asked.
7. **Fold.** The reply arrives through `SurfaceIntentInterpreter` against this
   surface's decision set. A fact becomes an `ELICITED_STATEMENT` for the cell; a steer
   becomes a `SUSPENDED_CONSTRAINT`; *assume* becomes a `PlannerAssumption` for the
   cell, `filed_by="user"`. **Every open cell is re-classified**, not only the affected
   row: the fixture showed one missing duration reported by eleven cells, and a
   duplicate that stays open after its gap closed is a question the user already
   answered. After the snapshot save succeeds, the host observes user-sourced
   statements and suspensions into memory (below). State advances on durable success,
   never on intent.
8. **Repeat** from 3, or emit `GateMet`.

**After `GateMet`:** the card offers Next. The next message is classified against the
same surface. A refusal, a steer, or a new fact **re-enters the loop**: every open cell
is re-classified and the gate re-evaluated, because a new fact can uncover a cell.
Anything else is consent: the kernel advances *and handles that message in Stage 2*, so
"let's plan, deep work first" is one message, not two. Silence does nothing.

**Never re-ask.** A cell whose probe was answered, or declined by assumption, is not
asked again in this session. This is `deployment.md`'s rule and #200's guard. A denied
assumption is the one exception, and it is not one: a denial is the user asking to be
asked.

### The Stage 1 surface's decision set

Offered to `SurfaceIntentInterpreter` as `allowed_decisions`; the model picks; every
button has the typed equivalent for free. Row refs for steering are **constraint
uids**, offered as option ids: you steer a rule, not a group.

| decision | meaning | kernel message |
| --- | --- | --- |
| `provide_facts` | a new fact or an answer to the probe | `ProvidePlanningFacts` with an `ELICITED_STATEMENT` for the open cell |
| `steer_not_today` | suspend a named rule for this session | `ProvidePlanningFacts` with `SUSPENDED_CONSTRAINT{uid, reason}` at `suspend:{uid}`; dependents invalidated |
| `steer_always` | promote a session statement to durable memory | **asks first**, then `memory_observe`; before asking, the host checks the statement projects as a durable rule at all |
| `assume` | force past the open cell | `PlannerAssumption(requirement_id=cell, filed_by="user")` |
| `deny` | withdraw an assumption, the planner's or the user's | `DenyAssumption(assumption_id)`: removed, dependents invalidated, the cell re-opens |
| `next` | consent to close the stage | `Advance`, legal only on `GateMet` |
| `back` / `cancel` | as increment A | `GoBack` / `CancelSession` |

`next` when the gate is not met is not in the offered set, so the interpreter cannot
return it. Typing "just move on" against an unmet gate is read as `assume` for the open
cell, which is what the person meant, made visible.

## Content contract

What the Stage 1 card must render. **This is the acceptance test for #266**; the card
grammar decides *how*, this decides *what*. The parallel brainstorm targets this
section by name and has confirmed it targets `StageCard` and `map_outcome(outcome,
snapshot) -> StageCard`, extended.

1. **Context**: active constraints grouped by anchor name, each row tagged with
   necessity (`must`/`should`) and applicability (every day / day-specific / *suspended
   today*). Unanchored rules in their own group; unanchored and unreachable are
   different things, and on the fixture day the unanchored group is a third of the
   rows. Rendered from the snapshot's `applicable_constraints`, the same rows the
   planner receives, same order (#202). **Session suspensions are rows**, in their
   group, with the reason *"you said: not today"* and a restore control that re-states.
   **Memory-side suspensions are not rows**: on a vacation day that set is every
   working rule. They render as one typed line under the *what today is not* heading,
   *"12 working-day rules off because today is a vacation day"*, from the count and
   the day type.
2. **Decided**: facts stated this session and assumptions filed, each showing who
   filed it (`filed_by`), each with a deny control and a `ref`.
3. **Asking**: at most one open probe, with `why_needed`; buttons when closed, free
   text otherwise.
4. **Gate**: one line, always, rendered from the typed `Gate`: *"still need: …"* or
   *"that's what I know to ask about a working Tuesday, anything else, or shall I
   plan?"*
5. **Controls**: Back, Cancel always; **Next only on `GateMet`**; every button with a
   typed equivalent.

A grammar that needs per-stage special casing to render these five has failed the test.

The card's own questions, deliberately not decided here: the token budget and how it
splits between Context and Asking (memobase's `context()` does `max_token_size × ratio`
with spill-over and preferred topics as a ranking), how anchor groups rank when 41 rows
do not fit, and the overflow design under Slack's 40-block cap.

## Steering semantics

- **Session-scoped by default.** *Not today* is a `SUSPENDED_CONSTRAINT` fact in the
  snapshot. **The fact is the only thing that binds**: the host filters the brief's
  `applicable_constraints` by it, so the planner never sees the rule; the card renders
  the row as suspended from the same fact. One fact per rule, so a second "not today"
  is a no-op and restore is deleting the fact by id. Nothing in memory changes.
- **The feedback channel.** After the snapshot save succeeds, the host observes every
  user-sourced `ELICITED_STATEMENT` and `SUSPENDED_CONSTRAINT` into memory on its own
  channel, with provenance (rule uid, session, what the user did). This is *reader
  feedback with provenance*, the half of a SAGE-style writer/reader loop this system
  can have: **recorded, never acted on.** No promotion, no counter, no score. #140 rules
  outcome data a rollback monitor only; a counter nobody reads is dead (memobase's
  `update_hits`). Assumptions and refusals are not observed: an assumption is about
  today, a statement is about the user.
- **Promotion is the one irreversible write and asks first.** *Always* goes through
  `memory_observe`, which is append-only and permanent. The agent confirms before
  writing, every time, and before asking checks schema fit: does this statement project
  as a durable rule at all. There is no "always" button that writes on press.
- **Correction** (*this rule is wrong*) routes to the memory server's correction path
  when map B lands. Until then it is *not today* plus a note; the design does not invent
  a correction mechanism the server does not have. The model never deletes.

## Where the loop runs: the open parameter

Spiked, not decided. What is **invariant** regardless of the winner, so the spec is not
held hostage:

- the matrix shape, its storage as a snapshot fact, and the arithmetic gate in the kernel
- the `GateMet` outcome, the typed `Gate`, and the rule that Next appears only on it
- the surface decision set above, and that replies go through the interpreter
- the fact kinds and their stable ids
- the seam types, if the split wins: `ProbeRequest(cell, context) → ProbeText` and
  `Reply(text, cell) → PlanningFactDraft | Refusal | Steer`

What the spikes decide: whether classify and generate are host-side typed calls in
`resolve` (**A′**), a skill-driven agentic loop in the harness (**B**), or split at the
seam, host classify and harness-written probe text (**C**). A′ replaces the original
"kernel-side" A: the kernel has one port, `PlannerPort.produce`, and the repo's precedent
for a judgement the catalog must see is `DayFrameJudge` in the host. Measure 4 (gate
testable with a stubbed judge) holds for A′ by construction; the A′/C comparison then
isolates one variable, who writes the probe. #283 holds the fixture, the five measures
and the decision rule: **A′ meeting thresholds forfeits B; C runs regardless.**

## Error handling

- **Memory unavailable** → typed `AdaptiveDependencyUnavailable` naming the store,
  exactly as #226 does for the calendar. Absence and failure never read as data.
- **The classify batch completes before any write.** A turn either folds whole or not at
  all. Within a completed batch, a cell whose call failed is `uncovered`. Fail *toward
  asking*, never toward silently skipping.
- **A judge returns something outside its schema** → the cell is `uncovered` and the
  failure is logged locally with the raw response. Exported metrics carry cell ids and
  counts only, never text.
- **The interpreter cannot place a reply** → the existing `SurfaceIntentError`; the card
  asks again with the same options. No fallback to keyword reading.
- **`next` arrives with the gate unmet** (a stale press, a race) → refused by revision
  binding as any stale press is; never silently advanced.
- One policy, stated once. memobase surfaces failures three ways in one pipeline; that
  is the shape to avoid.

## Testing

Two kinds, both required (CLAUDE.md).

**Unit, stubbed judge, offline.** The gate is arithmetic, so it is tested as arithmetic:
feed a matrix, assert `GateMet` or `AwaitingUser(cell)`. Ranking the same way. The
interpreter's decision set is tested by asserting which decisions a Stage 1 surface
*offers*, not which it picks. Stubs return the judge's raw wire shape, junk included,
and a one-element `side_effect` makes a second call raise, so **the fold's call count is
asserted structurally**: re-classify every open cell means exactly that many calls.
Every guard is mutation-verified: neuter it and watch the test fail before trusting it.
`tests/conftest.py` pins `FF_TIMEBOX_BACKEND=legacy` globally; these tests opt in like
`test_timebox_session_surface.py:81`.

**Eval, live model, resampled.** Gap recall and precision against Hugo's hand-labelled
set on the three fixture days, **n = 5**, reported as rates. Probe framing quality
judged by the non-contender `google/gemini-3.1-pro-preview`, n = 5; the contender is
`google/gemini-3.6-flash` at `minimal` for every spike, so measure 3 compares like with
like. A prompt change to the floor or the criteria is validated here, never by a green
unit suite. The two spike scripts under `scripts/spikes/` are the first two evals.

**Not tested by a pattern.** No test asserts an exact probe string. No test compares
Hugo's words to anything.

## Out of scope, deliberately

- **The card grammar**: #266, in parallel, with this document's content contract as its
  acceptance test.
- **Anchor hierarchy**: #140. Flat anchors are correct for a card.
- **Anchor dedupe at mint time**: memory server, filed separately.
- **The memory correction path**: map B. Steering routes there when it exists.
- **Cold start** for a user with no anchors: the floor still produces probes; that is
  the floor's job.
- **Stages 2 to 5.** The loop shape may generalise; nothing here claims it does.

## Sequence

1. **Relink pass** over the eight durable rules whose observations name anchors, on the
   live store, with a dry run first. Before the spikes, because the unanchored row is
   where a real conflict came from.
2. Hugo corrects the concern-floor (including the six planning-meta rules), hand-labels
   the fixture gaps, and fixes thresholds (#283). Before, not after.
3. Phase 1 groundwork lands so every spike reads the same shapes: `AnchorRef` on the
   view and the service join; the three fact kinds; `Gate`, `GateMet`, `stage1_gate`;
   `stage` on requirements and the forty cell requirements; `filed_by` and
   `DenyAssumption`; `applicable_constraints` on the snapshot; the Stage 1 decision set;
   the shared fixture.
4. Spikes A′ and C run; B only if A′ misses.
5. #266 lands its grammar on `StageCard`; this document's content contract targets it.
6. Increment B: the winning loop, on the chosen grammar, with the eval numbers as the bar.
