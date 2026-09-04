# Stage 1 as elicitation: collecting the constraints that hold today

**Status:** design, awaiting Hugo's review. Governs #262. Feeds #266. Loop location is spiked in #283–#286 and left as a parameter here.

## The defect

Stage 1 ("Constraints") completes the moment the day type is confirmed, with a ✅ and nothing shown. The requirement catalog has no user-owned requirement in Stage 1; the calendar and constraint reads are system-owned and satisfy themselves silently. The planner is handed ~22 rules for a working day and honours them; the user sees a count that is never rendered, and the session root reads *"No active constraints yet"* because it queries a legacy store nothing writes.

The legacy agent's Stage 1 — *gather fixed events, commutes, arrivals, habits scope, energy profile, sleep target; confirm "LOCKED?" before moving on* — was the planner showing the user what it knew and asking them to correct it before planning. That is what is gone.

Hugo's requirement adds one thing legacy never had: the *kinds* of constraint must not be a list in a prompt. A new kind he states should appear in Stage 1 without anyone editing anything.

## Decisions

Each was a fork in the brainstorm. The choice and the reason, so nobody re-derives them.

| fork | choice | why |
| --- | --- | --- |
| what Stage 1 does | **show, and fill gaps dynamically** | show-only never notices what is missing; a checklist of what is missing is the disease one level up |
| where the spec comes from | **layered: authored concern-floor + anchors from memory** | memory records what was said, never what was not; the floor is the only way to ask about something never mentioned; anchors make the floor grow irrelevant over time |
| when Stage 1 ends | **the user ends it; the agent proposes** | the whole point of eliciting is that the other party does not close the door; a cap is a magic number deciding how much of the day the agent may learn |
| what consent looks like | **the next message**, never a timer | in async Slack silence means done, thinking, or gone; a timer acts while the user is away — the "day nobody saw" shape one stage early |
| how to force past an unmet gate | **a user-filed assumption** | `PlannerAssumption` already satisfies a requirement by assumption; forcing is the user supplying the missing fact as one, visible and reversible, not a bypass |
| how the criteria run | **classify, then generate** | the paper's path and the one that beat human interviewers; the classify batch *is* the gap detector, and it is parallel |
| where the loop runs | **spiked** (#283–#286) | not predictable by reasoning; A is forfeited-against, C runs regardless for composability |

## The spec the agent reasons against

Two layers. The first is authored once and is the only hardcoded thing in this design. The second is the memory server's and grows on its own.

### Layer 1 — the concern-floor

Six concerns, at the level of *what a day has to have settled*, not what words describe it. Drafted from the clusters in the live anchor store; **Hugo corrects this before any spike runs** (#283).

1. **How the day is bounded** — when it starts and ends, what frames it.
2. **What is fixed** — events, appointments, arrivals that do not move.
3. **Movement and transitions** — commutes, travel, the gaps between fixed things.
4. **Body** — food, sleep, energy, exercise; the physical constraints on attention.
5. **Fragile intentions** — the things that only happen if protected; the reason a planner exists.
6. **What today is not** — suspensions: rules that usually hold and do not today.

A new *kind* of constraint slots under one of these without editing the floor. A genuinely new *concern* is a floor edit and should be rare; when it happens, the spike results will show it as a recurring probe nothing covers.

### Layer 2 — anchors

The memory server mints an anchor for every statement at write time, by a model, once (`memory_observe` → `resolve_anchors`). The live store holds 29 — `gym` 65 links, `dinner` 59, `deep work` 27, `sleep`, `commute`, `lunch`, `nature reservation`, `finance`… — every one from Hugo's own words. **Anchors are the categories.** A rule stated tomorrow about a thing never mentioned before mints a new anchor and appears in Stage 1 under it. No list is edited.

`anchor_edges` is empty by design (#140): no hierarchy, so `gym` and `run` sit side by side rather than under *exercise*. Flat is correct for a card. The tree is a separate, gated decision and this design does not depend on it.

One field change makes anchors reachable: `ConstraintView` is *"deliberately narrow: the patcher renders these and nothing else"* and `to_view()` drops anchors. Add `anchors: list[str]`. It is a join on uids the system minted — arithmetic, read-path safe, invariant I1 holds.

### The fourteen criteria

Verbatim from [Singhal et al.](https://github.com/anmolsinghal98/Requirements-Elicitation-Follow-Up-Question-Generation), whose criterion-guided questions beat human interviewers.

**Coverage criteria** — five. Each is a question the agent asks *about the conversation so far*, per concern. They are content-agnostic: "have they justified their assumptions about *sleep*?" works for an anchor that did not exist yesterday.

- tacit assumptions elicited
- alternatives considered
- the unclear clarified
- the contradictory clarified
- tacit knowledge elicited

**Framing criteria** — nine. Each is a property of a probe's *text*. They constrain generation and never run as a separate pass (decision: classify-then-generate, not generate-then-critique).

- not generic or domain-independent · not too long · no jargon · not technical · appropriate to the person · does not ask for solutions · one kind of requirement at a time · not open to multiple readings · not so vague it means nothing

### Coverage matrix and gate

The matrix is `concern × coverage-criterion`, thirty cells, each `covered | uncovered | not_applicable`. It is a **fact in the snapshot** so Back, redo and a restart all see the same state.

- A cell is `not_applicable` when its concern has no applicable rule from memory *and* no stated fact this session — there is nothing to have assumptions about.
- **Gate met** ⇔ no cell is `uncovered`. Arithmetic. No model call decides it.
- `GateMet` is a new kernel outcome, sibling to `AwaitingUser`. It carries one line: what would still help, if anything. The renderer offers **Next** only on this outcome.

## The plan the agent follows

One iteration of the loop, per turn:

1. **Read.** `plan_read` for the locked day; `memory_get_active_constraints(day, day_type)` with anchors; `memory_get_session_constraints(session_id)` for what this thread already established. All three are independent and run concurrently.
2. **Classify.** For every cell not already `covered`, one narrow typed call: given the conversation and the rules under this concern, is this criterion met? Structured output, a `Literal` per cell, nothing else. **All cells in one parallel batch** (CLAUDE.md) — thirty independent judgements are one round trip, not thirty.
3. **Rank.** Uncovered cells ordered by expected value: a concern with applicable rules or stated facts before one with neither, then a concern carrying a `must` before one carrying only `should`s, then the criterion order above. Every term is a count over minted fields; nothing in the floor is marked "hard" by hand. A user who stops after two probes has seen the two that mattered most.
4. **Generate.** One probe for the top cell, with the nine framing criteria in the prompt and the grounding clause **"based only on what the user has said"** — the clause that stops a probe inventing a concern never raised. Options are offered as buttons only when the answer set is closed (≤ 4).
5. **Ask.** `AwaitingUser(requirement_id=cell, question, why_needed, options)`. The existing `PendingBlocker` binds the answer to the question that was asked.
6. **Fold.** The reply arrives through `SurfaceIntentInterpreter` against this surface's decision set (below). A fact becomes a `PlanningFact`; a steer becomes a session fact; *assume* becomes a `PlannerAssumption` for the cell, filed by the user. The matrix is re-classified for the affected concern only.
7. **Repeat** from 2, or emit `GateMet`.

**After `GateMet`:** the card offers Next. The next message is classified against the same surface. A refusal, a steer, or a new fact **re-enters the loop** — the affected concern is re-classified and the gate re-evaluated, because a new fact can uncover a cell. Anything else is consent: the kernel advances *and handles that message in Stage 2*, so "let's plan, deep work first" is one message, not two. Silence does nothing.

**Never re-ask.** A cell whose probe was answered — or declined by assumption — is not asked again in this session. This is `deployment.md`'s rule and #200's guard, and this loop leans on it harder than anything before it because it has more turns.

### The Stage 1 surface's decision set

Offered to `SurfaceIntentInterpreter` as `allowed_decisions`; the model picks; every button has the typed equivalent for free.

| decision | meaning | kernel message |
| --- | --- | --- |
| `provide_facts` | a new fact or an answer to the probe | `ProvidePlanningFacts` |
| `steer_not_today` | suspend a named rule for this session | session fact superseding the rule; dependents invalidated |
| `steer_always` | promote a session statement to durable memory | **asks first**, then `memory_observe` |
| `assume` | force past the open cell | `PlannerAssumption(requirement_id=cell)` filed by the user |
| `next` | consent to close the stage | `Advance` — legal only on `GateMet` |
| `back` / `cancel` | as increment A | `GoBack` / `CancelSession` |

`next` when the gate is not met is not in the offered set, so the interpreter cannot return it. Typing "just move on" against an unmet gate is read as `assume` for the open cell — which is what the person meant, made visible.

## Content contract

What the Stage 1 card must render. **This is the acceptance test for #266**; the card grammar decides *how*, this decides *what*. The parallel brainstorm targets this section by name.

1. **Context** — active constraints grouped by anchor, each row tagged with necessity (`must`/`should`) and applicability (every day / day-specific / *suspended today* with the reason). Unanchored rules in their own group — unanchored and unreachable are different things. Rendered from the same `applicable_constraints` the planner receives, same order (#202).
2. **Decided** — facts stated this session and assumptions filed, by the planner or by Hugo, each with a deny control and a `ref`.
3. **Asking** — at most one open probe, with `why_needed`; buttons when closed, free text otherwise.
4. **Gate** — one line, always: *"still need: …"* or *"that's what I know to ask about a working Tuesday — anything else, or shall I plan?"*
5. **Controls** — Back, Cancel always; **Next only on `GateMet`**; every button with a typed equivalent.

A grammar that needs per-stage special casing to render these five has failed the test. The section grammar (#266 shape A) maps onto them one-to-one; this is a recommendation to that brainstorm, not a ruling.

## Steering semantics

- **Session-scoped by default.** *Not today* is a fact in the snapshot that supersedes the rule for this session. The planner's next brief omits it. Restore is re-stating; there is no delete.
- **Promotion is the one irreversible write and asks first.** *Always* goes through `memory_observe`, which is append-only and permanent. The agent confirms before writing, every time. There is no "always" button that writes on press.
- **Correction** (*this rule is wrong*) routes to the memory server's correction path when map B lands. Until then it is *not today* plus a note; the design does not invent a correction mechanism the server does not have.

## Where the loop runs — the open parameter

Spiked, not decided. What is **invariant** regardless of the winner, so the spec is not held hostage:

- the matrix shape, its storage as a snapshot fact, and the arithmetic gate
- the `GateMet` outcome and the rule that Next appears only on it
- the surface decision set above, and that replies go through the interpreter
- the seam types, if the split wins: `ProbeRequest(cell, context) → ProbeText` and `Reply(text, cell) → PlanningFactDraft | Refusal | Steer`

What the spikes decide: whether classify/generate are kernel-side typed calls (A), a skill-driven agentic loop (B), or split at the seam (C). #283 holds the fixture, the five measures and the decision rule: **A meeting thresholds forfeits B; C runs regardless.**

## Error handling

- **Memory unavailable** → typed `AdaptiveDependencyUnavailable` naming the store, exactly as #226 does for the calendar. Absence and failure never read as data. Stage 1 does not proceed on an empty rule set that might be a dead server.
- **Classify batch partially fails** → the unclassified cells are treated as `uncovered`. Fail *toward asking*, never toward silently skipping: an extra question is visible and correctable, a skipped concern is not.
- **A judge returns something outside its schema** → the cell is `uncovered` and the failure is logged with the raw response. No parsing of prose.
- **The interpreter cannot place a reply** → the existing `SurfaceIntentError`; the card asks again with the same options. No fallback to keyword reading.
- **`next` arrives with the gate unmet** (a stale press, a race) → refused by revision binding as any stale press is; never silently advanced.

## Testing

Two kinds, both required (CLAUDE.md).

**Unit, stubbed judge, offline.** The gate is arithmetic, so it is tested as arithmetic: feed a matrix, assert `GateMet` or `AwaitingUser(cell)`. Ranking is tested the same way. The interpreter's decision set is tested by asserting which decisions a Stage 1 surface *offers*, not which it picks. Every guard here is mutation-verified: neuter it and watch the test fail before trusting it. `tests/conftest.py` pins `FF_TIMEBOX_BACKEND=legacy` globally; these tests opt in like `test_timebox_session_surface.py:81`.

**Eval, live model, resampled.** Gap recall and precision against Hugo's hand-labelled set on the three fixture days, **n = 5**, reported as rates. Probe framing quality judged by a non-contender model, n = 5. A prompt change to the concern-floor or the criteria is validated here, never by a green unit suite. These are #283's measures 1 and 2 and the spikes produce the first numbers.

**Not tested by a pattern.** No test asserts an exact probe string. No test compares Hugo's words to anything.

## Out of scope, deliberately

- **The card grammar** — #266, in parallel, with this document's content contract as its acceptance test.
- **Anchor hierarchy** — #140. Flat anchors are correct for a card.
- **The memory correction path** — map B. Steering routes there when it exists.
- **Cold start** for a user with no anchors: the floor still produces probes; that is the floor's job. Beyond that, nothing.
- **Stages 2–5.** The loop shape may generalise; nothing here claims it does.

## Sequence

1. Hugo corrects the concern-floor and hand-labels the fixture gaps (#283). Thresholds fixed before, not after.
2. `ConstraintView.anchors` lands — one field, one test — so every spike reads the same view.
3. Spikes A and C run; B only if A misses.
4. #266 lands its grammar and names the rendering interface; this document's content contract targets it.
5. Increment B: the winning loop, on the chosen grammar, with the eval numbers as the bar.
