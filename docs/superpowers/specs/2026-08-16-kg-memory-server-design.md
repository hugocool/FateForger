# KG Memory Server — Design Spec

**Date:** 2026-08-16
**Status:** Approved (design); map charted as wayfinder map B
**Companion map:** GitHub `MAP: Self-improving KG memory server`
**Related maps:** #121 (map A — timebox MCP server), map C (agent re-implementation, not yet charted)

## Destination

> I never restate a preference. The system knows when it applies. When it misses one, it
> learns the *category* it was missing — and proves the category was right before keeping it.

A standalone, agent-agnostic memory server. Bound two ways: as an MCP server, and directly
in-process. Retrieval is deterministic anchor traversal over a typed graph, not vector
similarity. Timeboxing is consumer #1, not the owner.

The map is done when both moments below work and the way to them holds no open decisions.

**Moment 1 — loop 1.** Sunday has hockey. Oats appear at T−2h. The user said nothing.

**Moment 2 — loop 2.** The user supplies a preference the system missed. It proposes `sport`,
covering hockey and fitness, excluding commute. The proposal survives the gate. The vocabulary
is permanently better.

## The two loops

**Loop 1 — live, inside the agent turn.** Retrieval by anchor traversal. The day's calendar
hands the system symbols (`hockey`); it seeds on them, walks typed edges (`IS_A`,
`APPLIES_TO`, `WHEN_*`), and returns the constraints that resolve. Canonicalisation happens on
write: a new mention is projected onto the existing anchor vocabulary so that the slug written
at extraction time is the slug retrieval seeds on.

**Loop 2 — offline, proof-gated.** Induce the taxonomy of anchor *kinds* from accumulated
observations. `oats before fitness` + `oats before hockey` → propose `sport`. Reject `commute`
as a sibling. The proposal changes L3, which triggers re-projection of L2.

The loops appear to conflict — loop 1 collapses variation so retrieval hits, loop 2 needs
variation preserved so it can induce. **They only conflict if canonicalisation is destructive.**
See invariant I2.

## Three layers

```
        ┌──────────────────────────────────────────────────┐
        │  L3  TAXONOMY          slow · proof-gated        │
        │  anchor kinds · IS_A hierarchy · slug registry ──┼──► timebox server (#128)
        └───────────────────────┬──────────────────────────┘
                                │ governs the projection
   utterance ──┐                ▼
   cal event ──┤   ┌────────────────────────────────────┐
               ├──►│  L2  CANONICAL     fast · derived  │
               │   │  Constraint nodes · minted uid     │
               │   │  IS_A / APPLIES_TO / WHEN_*        │
               │   └────────────────┬───────────────────┘
               ▼                    │ deterministic walk
   ┌───────────────────────┐        ▼
   │ L1  OBSERVATIONS      │   resolve(date, anchors_present)
   │ immutable · raw       │        │
   │ append-only           │        ▼
   └───────────▲───────────┘   timeboxing agent / patcher
               │                    │
               └── outcome events ◄─┘   accept · undo · override
```

L2 is **derived** from L1 under L3. That is the load-bearing property of the whole design:
changing the taxonomy is a re-projection, not a data migration.

## Invariants

Stated so they can be enforced rather than eroded. A change that violates one of these is a
design change, not an implementation detail.

- **I1 — The LLM proposes and names. The lattice decides structure.** No LLM call in the read
  path; read-time traversal is a deterministic graph walk. Protects the interactive latency
  budget (#93) and is grounded in measured LLM ceilings (see Evidence).
- **I2 — Canonicalisation is a projection, never destructive.** L1 is immutable and
  append-only. This is what stops the two loops from starving each other.
- **I3 — Identity is minted, never content-derived.** Opaque, stable across edits. Semantics
  (slugs, `frame_slot`) layer on top of identity; they never substitute for it.
- **I4 — A taxonomy change is a re-projection, not a migration.** Follows from L2 being derived.
- **I5 — Every write is compare-and-swap.** Never blind replay of a prior payload.
- **I6 — Promotion by structure, rejection by statistics.** Statistics may veto a taxonomy
  change; they may never authorise one. See Gate.

## Retrieval

Anchors arrive as **symbols**, not natural language — a calendar event hands the system
`hockey` directly. Every system surveyed embeds the query first because its input is natural
language. That step is dead weight here.

Conditional applicability is **graph structure, not a field**. Multi-seed traversal, where
rules sitting at path intersections are exactly the conditionally-applicable ones: co-presence
*is* path intersection, so no separate condition-evaluator is required.

## Gate (loop 2)

Counterfactual improvement estimation is **provably invalid** on the existing logs: the
retriever is deterministic, so the logging policy supports only itself, and the IPS bias equals
the entire expected reward on unsupported actions — in the limit of infinite data. More
sessions do not help. The gate is therefore shaped as:

**Promote** — structural, not statistical:
- the proposed concept is lattice-legal (closure of its instance set; siblings pairwise
  disjoint and unioning to the parent)
- it survives an OntoClean rigidity/identity check (this is what rejects `commute` as a parent
  of `cycling`: commute is a *role*, anti-rigid, not a kind)
- the user confirms via binary-split questioning

**Reject** — statistical, veto-only:
- leave-one-out influence replay as a regression filter (computable today, no labels required:
  re-run the planner with each retrieved constraint removed; an unchanged plan means the
  constraint was inert, which measures flooding directly)
- outcome data (accept/undo) as a **rollback monitor**, never a promote gate — detecting a
  20%→15% undo shift at 80% power needs ~900 sessions per arm

**Two evidence channels that never merge.** An explicit user "no" carries infinite penalty and
may contract the logical extent. Behavioural non-compliance carries finite weight, adjusts
confidence only, and never touches the extent. Skipping oats before one hockey game is not
evidence the rule is wrong.

**Decompose or be unfalsifiable.** Worst-case bound width equals the disagreement rate, so a
change touching 5% of sessions is estimable and one touching 60% is not. Taxonomy changes are
gated as small independent diffs.

**Guard against overfitting the gate.** Repeatedly scoring generated taxonomies against a fixed
session set is adaptive data analysis; it needs a query budget and holdout discipline.

## Two tiers, two failure modes

Settled empirically (see Experiments). The store serves two failure modes that want opposite
things, and they must not share a tier.

- **FM1 — turn-to-turn.** The agent extracts what to retain per turn rather than carrying
  history, so restating *within* a session means extraction dropped it. Vocabulary:
  `hockey game`, `hospital visit sequence`, `admin and taxes`. Today's facts.
- **FM2 — cross-session.** Durable preferences not retrieved on later days. Vocabulary:
  `wake up time`, `morning ritual`, `commute`, `lunch`, `deep work blocks`.

**Session tier** — fast, total recall, dies at session end, no canonicalisation.
**Durable tier** — canonical, gated, traversable.

### Promotion is the load-bearing operation

Currently ~2% functional: **13 of 625 session concepts ever reached PROFILE.** `wake up time`
was extracted 22 times across 22 threads, always session-scoped, never promoted, under six
un-canonicalised surface forms. That single defect explains FM2 without invoking any graph
mechanism.

Promotion is **not row-level**. *"I have hockey at 11:45 and I need oats two hours before"* is
one utterance carrying both tiers — a session fact and a durable rule. Promotion is
generalisation: loop 2 happening at write time.

**Two paths, empirically complementary:**

| Path | Rule | Measured |
|---|---|---|
| **Recurrence** (observed) | promote a rule when **its anchor** is durable, not when the rule recurs | recall 0.85 against known PROFILE rules |
| **Assertion** (declared) | an explicitly stated rule promotes immediately, no evidence threshold | covers exactly the 10 rules recurrence misses |

The recurrence path's false negatives are *all* policy declarations — `Client attendance days`,
`WIP guardrail`, `C2F framing cap 15m`, `Sci-Fi Reading`. None would ever recur, because
declaring something once is how declarations work. **Recurrence catches what the user does;
assertion catches what the user decides.**

**Anchors come from the world, not from talk about the world.** An anchor vocabulary derived
from constraint text alone scores `gym` at 0 threads despite `Oats −2h before gym` being among
the most locked-in rules; `hockey` 5, `meeting` 5, versus `oats` 17, `commute` 41, `wake` 59.
Gym, hockey and meetings live in the calendar. Anchor discovery must treat the calendar as a
first-class source or it systematically misses the anchors that work so well they are never
discussed.

**The gate must reject meta-level rows.** Currently written as constraints: *"the user wants to
begin the timeboxing session immediately"*, *"the activity must adhere to a timeboxing
format"*. These describe the session, not the day.

### Decay is a discount rate on evidence, not a lifetime

Constraints do not share a half-life, and the difference is not a tier — bucketing it into
sprint / season / permanent fails because the boundaries are different for every kind.

Model it instead as **how much an observation from N days ago still counts**, with the rate
inherited from the *kind* of thing rather than set per row:

| kind | discount on old evidence |
|---|---|
| sleep window | ~zero — a year-old observation still counts fully |
| commute duration | slow — but a run of contradicting observations overtakes it |
| sprint focus (C2F) | steep — three-week-old evidence is nearly worthless |
| today's hockey | total — worthless tomorrow |

One mechanism, four behaviours, no boundary to define.

**Why this shape and not expiry:**

- **Nothing is deleted, it fades.** The observation stays in L1; its weight declines. Consistent
  with I2.
- **It is Spohn/Shenoy conditionalisation**, already chosen for the update rule: an observation
  contributes *x*, older ones contribute less, the discount rate is per-kind. Reversible and
  commutative, so replay order still does not matter.
- **Rigidity predicts the rate.** Rigid kinds (`sport`, `sleep`) take a low discount;
  anti-rigid roles (`commute`, `sprint focus`, `client`) take a high one. The same OntoClean
  check now does three jobs: taxonomy validity, decay rate, and rejecting `commute` as a parent
  of `cycling`.
- **No timer to get wrong.** The C2F rules need no sprint boundary defined — they simply stop
  carrying weight when nothing re-observes them, while `sleep 23:00` retains weight from a
  January observation.
- **It puts `valid_at`/`invalid_at` to work** — the one genuinely useful Graphiti primitive
  that has never been used.

This explains #116 without new mechanism. `C2F framing cap 15m`, `Artifact-first scheduling
gate` and `Facet Extraction Blocks` (×44) are `LOCKED`/`MUST` and flood every session because
they were filed as permanent when they were sprint-scoped, and the scope vocabulary
(SESSION / PROFILE / DATESPAN) has no way to express "relevant to this chapter".

Initial rates are defaulted by kind, overridable, and adjustable at weekly review — which is
also the natural moment to say a sprint focus is finished.

### The operator surface is ambient

The agent **proposes** a tier assignment and the user can change it, without impeding the flow
of conversation. This is the Ripple-Down-Rules interaction — correction happens at the point of
error, inside normal work, as part of the work rather than an interruption to it.

**Consequence, and it is unfixable later:** "did not object" must never be recorded as
"confirmed". The reliability signal needs three values — **actively confirmed** / **actively
corrected** / **unexamined** — and only the first two are evidence. Otherwise the system learns
that everything it proposed was right, from silence, with the user's name on it.

**Assertions can be overruled by outcomes**, surfaced through the same ambient channel rather
than applied silently. This is the answer to AGM's success postulate: a declaration is not
permanently immune, but evidence proposes rather than overrides.

## Known constraints

Facts about the existing system the design must survive. These are constraints, not work items.

| | Constraint |
|---|---|
| C1 | Constraint identity is content-derived in 1,620 of 1,662 rows (97.5%). Editing a constraint changes its identity — precisely the event the learning loop must follow. |
| C2 | Constraints carry control flow, not just prompt text. Of 9 consumers of `session.active_constraints`, 5 are behavioural; `agent.py:3272` gates task prefetch on a planning-aspect id, uncovered by tests. |
| C3 | #104's second extraction runs on machine-generated text (`stage_user_message` cleared at four sites; RefineNode falls through to a synthetic instruction). Under I2, a fabricated observation is permanent. |
| C4 | Two stores. Slack constraint-review modals read the SQL table, not the durable store. |
| C5 | No outcome column in ~1,000 logged sessions, and much of the corpus ran while retrieval returned zero (#114) — a degenerate regime that must be segmented, not averaged. |
| C6 | "Anchor" already means three things here: retrieval seed, block label (#118), and the `planning_anchors` table. |
| C7 | Task-specific work constraints flood the profile store (#116) — visible in the data as `Facet Extraction Blocks` ×44, `Block Count` ×42. |
| C8 | Existing dedup lives at two layers; the store-level layer is replaced outright by projection, since semantic identity becomes a projection rule rather than a lookup-before-write. |

## Evidence

Load-bearing findings from the research pass. Full reports in `docs/superpowers/research/`.

- **LLM structural ceiling.** LLMs4OL 2024: term typing F1 0.97–0.99, but taxonomy *discovery*
  peaks at 0.6557, drops to 0.21 on DBpedia and 0.03 zero-shot; relation extraction 0.078.
  Grounds I1.
- **FCA ≡ version spaces.** Formal Concept Analysis with attribute exploration is the same
  algorithm as Mitchell's version-space specific boundary under Angluin counterexample queries.
  `{hockey, fitness}″` is simultaneously the FCA concept, the least general generalisation, and
  the S-boundary — one set intersection. Over-generalisation detection is free: if `commute`
  lands in the extent, the attribute vocabulary cannot separate them, which is a crisp
  schema-change trigger rather than a tuned threshold.
- **OntoClean is now automatable.** GPT-4 labels Rigidity and Identity at ~4% inaccuracy
  (GPT-3.5 was ~60% wrong). The philosophical check that rejects `commute` has been in the
  literature since 2002 and only recently became usable.
- **Deficient support.** Sachdeva, Su & Joachims (KDD 2020) — unbiased off-policy estimation
  requires the logging policy to support every action the target policy might take. Grounds the
  Gate section.
- **Offline/online correlation is weak.** Booking.com measured Pearson −0.1 (90% CI −0.45 to
  0.27) between offline gain and business value across 150 models. The gate is a regression
  filter, not an improvement detector.
- **LLM-judge retirement fails silently.** *The Blind Curator* shows judge-based memory
  retirement stops working past a false-pass threshold no amount of data can cross, surfacing
  in no aggregate metric.
- **Canonicalisation cascade worth reusing.** Graphiti ships exact-name → MinHash Jaccard ≥0.9
  → LLM-last. Canonical identity is fatal-if-wrong for anchor traversal and was the weakest
  link across every system reviewed.
- **Framework reality check.** All five candidate names are real, but four carried wrong
  mechanisms: `memg-core`'s "anchor" is the embedded field, not identity, and it has no
  canonicalisation; SAGE argues *against* anchor initialisation and requires training; GAM and
  Memora are two unrelated papers fused into one; memU has no graph. Only All-Mem's description
  survived intact.
- **Mem0 is a confirmed non-fit.** Its own Table 2 reports full-context 72.90 beating
  Mem0-graph 68.44. The claim is cost, not quality.

## Open questions (fog)

- Re-projection mechanics when L3 changes — incremental or full rebuild, and what invalidates.
- **Ambiguous non-compliance.** No prior art found. Recommender MNAR/PU literature assumes no
  stated preference exists, which is the opposite case. Genuinely open.
- Migration of the 1,662 existing rows.
- Whether Notion survives as a human-editable view onto the graph.
- Multi-user, or single-tenant forever.
- Tuning the loop-2 induction prompt itself.

## Out of scope

Rewriting the timeboxing agent (map C). Patcher and calendar sync (map A). The task↔block half
of #118 (map A). Outcome-event capture ships on map A as #123 against the existing patcher.
