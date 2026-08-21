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

- **I1 — The LLM proposes and names. The lattice decides structure.** No LLM call in *the
  constraint projection's* read path; read-time traversal is a deterministic graph walk.
  Scoped deliberately. `get_active_constraints` is model-free because planners call it inside
  a loop — an earned constraint, not a law of memory. A projection whose economics differ
  declares its own read-path contract: an admonishment read fires once per nag decision, not
  once per planning iteration. Stated as a universal, this would force a wrong design on the
  first projection that does not share the planner's latency budget. Protects the interactive
  latency budget (#93) and is grounded in measured LLM ceilings (see Evidence).
- **I2 — Canonicalisation is a projection, never destructive.** L1 is immutable and
  append-only. This is what stops the two loops from starving each other.
- **I3 — Identity is minted, never content-derived.** Opaque, stable across edits. Semantics
  (slugs, `frame_slot`) layer on top of identity; they never substitute for it.
- **I4 — A taxonomy change is a re-projection, not a migration.** Follows from L2 being derived.
- **I5 — Every write is compare-and-swap.** Never blind replay of a prior payload.
- **I6 — Promotion by structure, rejection by statistics.** Statistics may veto a taxonomy
  change; they may never authorise one. See Gate.

### Enforcing an invariant that no output reveals

Two of the above cannot be protected by asserting on behaviour, because the wrong
implementation returns the right answer.

- **I1's read path** is guarded by an AST test that walks `read_api` and fails if a judge call
  appears. A model call there would return correct constraints — just slowly, and differently
  on a second read of the same day.
- **The anchor walk's query plan** is asserted directly via `EXPLAIN QUERY PLAN`. SQLite has no
  cardinality estimate for a recursive co-routine, assumes it is large, and inverts the join to
  `SCAN ca` — making the walk linear in total store size rather than in the neighbourhood
  reached. `CROSS JOIN` forces the order: **85 ms to 0.43 ms, identical results.** Nothing
  behavioural can catch the keyword's removal, because nothing about the output changes.

The rule: **when correctness lives in something no output reveals, assert the mechanism.**

The corollary is the uncomfortable one. These are exactly the invariants a green suite cannot
protect — every finding in the 2026-08-17 sweep came from deliberately breaking something,
never from running the tests.

## Retrieval

Anchors arrive as **symbols**, not natural language — a calendar event hands the system
`hockey` directly. Every system surveyed embeds the query first because its input is natural
language. That step is dead weight here.

Conditional applicability is relational, not a field — a scalar `condition` column is
unqueryable. **This fork is now closed — #137 shipped path intersection.** The table below is
kept because it records what was traded away, and the cross-day case under it is the debt that
trade incurred:

| | conditionality lives in | the cross-day case |
|---|---|---|
| **Path intersection** (WaterCircles) | graph **structure** — co-presence *is* path intersection, so no separate condition-evaluator exists | needs a new temporal edge type |
| **Trigger predicates** (*Delivery, Not Storage*) | **predicates attached to rules**, evaluated deterministically | `temporal` is already a predicate class |

Both reach I1 by different routes. The cross-day case is real and was found in the data, not
imagined: a gym event described as *"post-hockey… Day after hockey → hams pre-fatigued, so
quads carry the volume."* Gym content depends on what happened **yesterday**, which multi-seed
same-day intersection cannot express.

An event carries *n* anchors, not one — four surface forms were observed for a single anchor
(`Hockey`, `Hockey training`, `Hockey Game (incl. warmup)`, `Hockey at vvv`), plus one event
titled `hockey/running` which is genuinely two. And offsets belong on the edge (`oats
APPLIES_TO sport, −2h`), not on the constraint, or a rule cannot carry a different offset per
anchor.

### Substrate — SQLite, decided by measurement (#141)

Recursive CTEs, no graph database. At **100× the real store, on disk, a depth-3 walk runs
0.79 ms p50** — less than a localhost round trip, so a server-based graph cannot win on
latency and loses on standalone-ness. Neo4j was deliberately not benchmarked; there is no
figure it could return that would change the decision.

The trap is recorded under *Enforcing an invariant that no output reveals* above: the obvious
query is linear in total store size, costs 0.87 ms today, and degrades invisibly as the store
grows. **A substrate decision was one missing `CROSS JOIN` away from being made wrongly.**

### The hazard while the graph is empty

With no edges, a constraint anchored to `sleep` is unreachable on any day whose events do not
name `sleep` — the walk returns nothing and the caller cannot distinguish *no such rule* from
*no path to it*. **Call without `anchor_uids` until #140 lands.** This is the thing a newcomer
gets wrong: the surface exists, so it reads as ready.

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

### The verdict procedure (#140)

The shape above is a policy; this is the procedure. **Checks run in this order, and the order is
load-bearing** — each stage is cheaper than the next and can reject alone, so a malformed
proposal never reaches a model call and a structurally illegal one never reaches statistics.

| # | check | cost | may reject | may authorise |
|---|---|---|---|---|
| 0 | **Diff-size bound** — a proposal touching more than the estimable fraction of sessions is split or refused, never adjudicated | free | ✓ | ✗ |
| 1 | **Lattice legality** — closure of the instance set; siblings pairwise disjoint and unioning to the parent | free | ✓ | ✗ |
| 2 | **Minimum support of 2 on the repair** | free | ✓ | ✗ |
| 3 | **Deletion brake `n > s/e`** — required evidence scales with the syntactic mass being deleted | free | ✓ | ✗ |
| 4 | **OntoClean rigidity/identity** — is the proposed parent a *kind*, or a *role*? | one model call | ✓ | ✗ |
| 5 | **Leave-one-out influence replay** — regression filter against the noise floor | expensive | ✓ | ✗ |
| 6 | **User confirmation** via binary-split questioning | one interaction | ✓ | **✓ — only here** |

**Nothing before stage 6 can authorise anything.** That is I6 made operational: stages 0–5 are
a sieve, and passing all of them means only *not yet rejected*. A proposal that clears every
structural check and every veto is still not promoted until the user confirms. Read the table
as six ways to say no and one way to say yes.

**Minimum support of 2 is on the repair, not on the rule** (EITHER, Mooney & Ourston). If a
correction applies to a single example, the example is the more likely error. This is the one
to build first: it needs no statistics, no labels and no outcome data, and it provably
strengthens as the corpus grows rather than requiring a corpus to begin working.

**A noise floor is a precondition, not a check** — a gate that cannot separate its own noise
from a real regression is worse than no gate, because it rejects proposals at whatever rate the
sampler happens to disagree with itself. **There are two floors, and they are not the same
measurement.**

**The judge-side floor is measured** (`2026-08-20-sampler-noise-floor.md`, two identical passes
over the real 69-observation corpus):

| field | unpinned | `temperature: 0` |
|---|---|---|
| `tier`, `decay_class`, `is_binding` | 0.0% | 1.4% |
| `days_of_week` | 1.4% | 1.4% |
| `label` (free text) | **44.9%** | **53.6%** |
| any field | 46.4% | 56.5% |

**Every categorical judgement is effectively deterministic; the whole-record figure is
paraphrase and nothing else.** Two runs render one rule as *"Oats before gym"* and *"Oats
timing"* and neither is wrong.

**So the diff-size bound is computed per field over categoricals — `tier`, `decay_class`,
`is_binding`, `days_of_week` — and never over whole records.** This matters more than it
sounds: whole-record gives ~46% and categorical gives ~1.4%, two orders of magnitude apart, and
**the obvious way to compute it is the wrong one.** Derived from 46% the bound admits almost
anything; derived from 1.4% it constrains. Stage 0 has no fixed constant either way — it is
derived from the measured floor and tightens as the sampler stabilises. A proposal exceeding it
is decomposed and resubmitted as parts, never waved through.

**The planner-side floor is not measured, and stage 5 needs it rather than the one above.**
Influence replay re-runs *the planner* with a constraint removed; its confound is planner
non-determinism, not judge non-determinism. The judge measurement unblocks stage 0 and makes
re-projection trustworthy (#154) — it says nothing about how much two identical planning runs
differ. Expect that floor to be **worse**, because a plan is mostly not categorical, and the
paraphrase effect that dominates `label` is the general case rather than the exception.
**Categorical determinism is the lucky special case.**

**Pinning `temperature: 0` does not buy determinism here.** It was not lower on any field, and
whole-record was higher. The single-field differences are one observation each and one pair of
runs cannot show that zero is *worse* — but it is enough to retire the assumption that pinning
delivers a stable draw. Plausibly the endpoint still samples reasoning tokens: `minimal`
reduces them and this API rejects `{"enabled": false}` outright, so there is no floor below
minimal. **Determinism comes from comparing the right fields, not from a sampling parameter.**

**Limit worth stating rather than discovering: replay cannot police free text.** Stage 5's
discriminating power rests on the compared fields being near-deterministic. A judgement
returning prose — a rationale, a proposed anchor *name*, a constraint label — sits above the
paraphrase floor and is invisible to the filter. **The gate is therefore blind to precisely the
part of a taxonomy proposal that carries its meaning**, and that part is the part a human reads
at stage 6. This is an argument for stage 6 being the only authorising step, not a gap in it.

**Drift is diachronic and does not belong in this procedure at all** (Leake & Wilson). A
population-level trend requires signed cumulative error exceeding a magnitude *and* persisting
for a duration before trend analysis runs — signed summation being the trick that matters,
since symmetric noise cancels while bias accumulates. A snapshot policy structurally cannot
make that call, so drift proposes changes *into* stage 0; it is never a check *within* it.

#### What a human can and cannot override

- **Overridable: every statistical veto** (stages 2, 3, 5). These are brakes, not authorities —
  they exist to slow a change the evidence does not yet support, and a user who says the rule
  is right outranks a count that says it is unusual. This is the AGM success postulate, and it
  is the same last-write-wins the rest of the design already commits to.
- **Overridable with an explicit acknowledgement: OntoClean** (stage 4). Rigidity is a model
  judgement at roughly 4% inaccuracy, so it is right far more often than a user is wrong — but
  it is not certain, and a person naming their own domain may legitimately know better.
- **Not overridable: lattice legality** (stage 1). Not because it is more authoritative, but
  because overriding it does not produce a taxonomy the user disagrees with — it produces a
  structure the read path silently answers wrongly from. Siblings that overlap make a traversal
  return a rule twice; siblings that do not union to their parent make it return nothing on the
  gap. **Both are invisible at the call site**, which puts this in the same class as the AST
  guard and the query-plan assertion: an invariant no output reveals.

**Forgetting is safer here than the CBR literature implies, and this is deliberate.** Smyth &
Keane's warning about utility-based deletion holds because a pure case-based reasoner has no
fallback generator — strip its cases and it cannot solve anything. There is an LLM behind this
memory, so a deleted constraint degrades a plan rather than breaking the system. The deletion
calculus is looser than imported caution suggests, which is why stage 3 is a scaling brake
rather than a prohibition.

**Retraction is still not free.** CRDR is the only Ripple-Down-Rules variant permitting true
rule modification, and to get it it had to abandon the cornerstone gate, add explicit state
tracking and conflict detection, and reintroduce precisely the maintenance-scaling risk RDR
existed to eliminate. Stage 3 is what keeps this system from paying that bill by accident.

**Everyone else refuses this adjudication, and that is the argument for stage 6 rather than an
embarrassment.** AGM hands it to the input unconditionally. Both TMS papers push it to the
problem solver by name. RDR pushes it to the human expert as explicit philosophy. IB3 and BBNR
exist because instance-based learning had to face noisy data and could not push it anywhere.
**A single contradiction is sufficient everywhere in classical belief revision, TMS and RDR** —
so a design that lets statistics accumulate against a user's stated rule would be the outlier,
not the rigorous choice.

### The verdict has three values, not two

`keep` / `retract` is the defect. **IB3** (Aha, Kibler & Albert 1991) supplies the third, and it
is the cleanest mechanism found anywhere in the survey — two parameters:

| condition | verdict | action |
|---|---|---|
| `lower(accuracy) > upper(class_frequency)` | acceptable | promote |
| `upper(accuracy) < lower(class_frequency)` | noisy | retract |
| intervals **overlap** | **mediocre** | **wait — gather more evidence** |

Three properties that make it right rather than merely plausible: the baseline is the class's
**own observed frequency**, so a rule about something rare is not punished for applying rarely;
thresholds are deliberately **asymmetric** (90% confidence to be accepted, 75% to be dropped);
and intervals **shrink monotonically**, so verdicts are provisional early and firm late with no
scheduling logic.

**Every system surveyed that lacks this third state is one mislabelled observation away from a
bad permanent edit** — FORTE, KRUST, base EITHER, JTMS, canonical RDR. The current store has two
states.

**IB3 is only half of it.** It can ask "is this item bad?" but never "who is to blame?".
**BBNR** (Delany & Cunningham 2004) is the causal half — it maintains a *liability set* of what
**causes** misclassifications rather than what **is** misclassified, verifies each removal by
simulation, and **restores automatically if the removal breaks anything**. They compose, and
each is unsound alone.

**The update rule is Spohn/Shenoy evidence-oriented conditionalisation**: `τ(A↑x) − τ(A) = x`.
Each observation of strength *x* moves the two-sided rank by exactly *x*; *n* observations by
*nx*. **Reversible by construction and commutative**, so replay order does not affect the
result — which is what makes a derived projection over an immutable log safe. Cite Definition
10 (evidence-oriented); Spohn's Definition 6 (result-oriented) explicitly does *not* accumulate.

**Simplest thing that works, and it needs no statistics:** EITHER's noise mode — **minimum
support of 2 on the repair, not on the rule**. *"If the correction only applies to a single
example, then it could well be that it was the example, rather than the rule, that was in
error."* Companion brake worth stealing: **the evidence required to justify a deletion scales
with the syntactic mass of what is being deleted.**

**Counter-based promotion is dead.** Nous's own ablation shows a confidence update with uniform
observation weights *"degenerates into a soft recency-follower"* and ties last-write-wins. This
invalidates `session_appearances >= 3 → LOCKED` from the earlier constraint-memory spec. The
distinction lives in the **reliability signal**, not the update rule.

**If an LLM judge appears anywhere, it needs a rubric.** Measured: an unguided judge ranks
learned rules at **46.4%**, and **15.8% on widest-margin pairs — an inversion, worse than
chance**. The same judge with an outcome-validated rubric reaches **73.8%**.

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

### Three source channels, and the channel is a free durability prior

| channel | mostly produces | prior |
|---|---|---|
| planning session | today's facts | session |
| **weekly review** | **policy and reflection** | **durable** |
| calendar | anchors, and behavioural outcome | — |

A statement made during weekly review is far more likely to be a durable rule than one made
mid-planning. That is the speech-act signal the assertion path needs, **derivable from context
with no classifier**. It also explains the assertion cases found empirically — `WIP guardrail`,
`Two-lane strategic day cap`, `Wednesday revenue-first precedence` all read as reflection, not
planning.

Infrastructure exists: `review-system/` is a FastMCP server (Notion-backed, Socratic gated
sessions, SKILL.md and AutoGen integration complete, ORM wiring outstanding), plus a tested
`revisor` agent. The weekly review is a recurring calendar ritual, not a hypothetical.

**This makes three stores, not two.** C4 already notes the SQL table and durable store
diverging; the review adds a Notion-backed third — and it is the channel producing the *most
durable* rules. If review-derived policy lives only in Notion it never reaches retrieval. Must
be decided, not discovered.

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

### Why tier is judged at write time but not stored there

A design question worth settling before promotion is built on top, because the code alone
cannot tell you which reading was intended.

Tier is asked of the model during ingest, alongside anchors, meta and dedup — but it is not
persisted on the observation. Under I2 and I4 that is correct rather than wasteful: **tier is
an L2 property, and L2 is derived from L1 by re-projection.** Writing a tier onto an immutable
observation would freeze a judgement that must be free to change when the taxonomy does.

So why ask at all, if the answer isn't kept? Because the **ambient proposal surface** needs an
answer at the moment of the observation. The agent proposes a tier and the user can correct it
without breaking the flow of conversation — and that proposal has to exist while the context is
still fresh. Re-deriving it later would mean the user sees nothing at the moment it would have
been cheapest to correct.

The judgement is therefore **transient by design**: it exists to be shown, not to be stored.
It also rides free in the existing `asyncio.gather`, so it costs no additional round-trip.
**L2 remains the authority**; a stored tier would compete with it.

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

## Experiments

Every number in this spec was measured against the real store (`data/admonish.db`, 1,662 rows),
the session logs (1,004 sessions), and the real calendar. Scripts are throwaway; the findings
are not.

| # | Question | Result |
|---|---|---|
| E1 | How many real concepts are in the store? | 1,662 rows → 606 concepts. Only 2.7× collapse. Naive jaccard-0.6 merging **false-merges** distinct concepts (`Work Window` with `Deep Work Block Duration`) — first evidence that a similarity threshold is the wrong canonicalisation tool. |
| E2 | Is restatement evidence? | **No.** 48% of restatements occur <1 min apart, median gap 1.2 min; only 11% span more than a day. Machine duplication is separable deterministically: rows ≫ threads is machine, rows ≈ threads is genuine. |
| E3 | Which failure mode dominates? | FM2 (cross-session retrieval) hits 95 distinct concepts; FM1 (within-session) hits 33. Their vocabularies are disjoint — FM1 is today's facts, FM2 is durable preferences. |
| E4 | Does promotion work? | **13 of 625 session concepts ever reached PROFILE (2%).** `wake up time` extracted 22× across 22 threads, never promoted. This is FM2's entire cause. |
| E5 | Does anchor-recurrence promotion work? | **Recall 0.85.** Its 10 false negatives are all policy declarations — which is exactly what the assertion path covers. |
| E6 | Does anchor traversal work? | Yes — two deterministic hops on one real week, no LLM in the read path. The hockey/oats failure is documented in the real calendar (2026-03-08), not hypothetical. |
| E7 | Are outcomes recoverable retroactively? | **Yes**, for machine-checkable rules, from the calendar, with zero new instrumentation: oats −2h before gym 83%, no-meetings-before-13:00 75%, lunch 62%, work-cutoff 100%. |
| E8 | Is durable memory contributing? | **84% of logged retrievals selected zero constraints.** Durable memory contributed to 1 of 32 checkable rule-days; 14 were honoured with nothing retrieved. Caveat: only the *durable* path is logged, and the rules audited are ones a competent planner does anyway. |

E8 is the uncomfortable one and should stay uncomfortable: **the baseline to beat is not the
current system, it is no durable memory at all**, and that baseline scored 62–100% on
everything checkable. If memory earns its keep it is on the unguessable rules — Friday systems
quarantine, C2F caps, Wednesday revenue-first — and none of those were tested.

## Build order

The write path first, not the graph. Retrieval was shown cheap (E6); promotion was shown broken
(E4), and it is the whole of FM2.

1. **L1 observation log + anchor vocabulary.** Append-only, minted identity (I3), machine-replay
   filter at ingest (C9). The anchor vocabulary comes out of the recurrence count already
   present in the data, plus the calendar (which is where `gym` and `hockey` live).
2. **Promotion.** Both paths — anchor recurrence and assertion — with the channel prior. Ambient
   proposal surface with three-valued reliability.
3. **Retrieval by traversal.** Deterministic walk. Only after #137 settles the conditionality
   encoding.
4. **Decay**, then **loop 2 and the gate**, in the weekly review.

Steps 1–2 fix FM2 on their own, run on SQLite, and require no substrate decision.

## Known constraints

Facts about the existing system the design must survive. These are constraints, not work items.

| | Constraint |
|---|---|
| C1 | Constraint identity is content-derived in 1,620 of 1,662 rows (97.5%). Editing a constraint changes its identity — precisely the event the learning loop must follow. |
| C2 | Constraints carry control flow, not just prompt text. Of 9 consumers of `session.active_constraints`, 5 are behavioural; `agent.py:3272` gates task prefetch on a planning-aspect id, uncovered by tests. |
| C3 | #104's second extraction runs on machine-generated text (`stage_user_message` cleared at four sites; RefineNode falls through to a synthetic instruction). Under I2, a fabricated observation is permanent. |
| C4 | Two stores. Slack constraint-review modals read the SQL table, not the durable store. |
| C5 | No *explicit* outcome column in ~1,000 logged sessions — but outcomes are recoverable retroactively for machine-checkable rules by auditing the calendar (see Experiments), and the review system's Outcomes DB may hold explicit ones. Much of the corpus ran while retrieval returned zero (84% of logged selections), a degenerate regime that must be segmented, not averaged. |
| C9 | Every apparent signal in the corpus needs a machine-replay filter before it means anything. Three separate measurements were contaminated: 48% of constraint restatements occur <1 minute apart, and user turns repeat byte-identically. Raw counts overstate evidence by roughly 2×. |
| C6 | "Anchor" already means three things here: retrieval seed, block label (#118), and the `planning_anchors` table. |
| C7 | Task-specific work constraints flood the profile store (#116) — visible in the data as `Facet Extraction Blocks` ×44, `Block Count` ×42. |
| C8 | Existing dedup lives at two layers; the store-level layer is replaced outright by projection, since semantic identity becomes a projection rule rather than a lookup-before-write. |

## Evidence

Load-bearing findings from the research pass. Full reports in `docs/superpowers/research/`.

**The two-layer design, validated on the real corpus.** Everything below this line is
measurement on Hugo's own store rather than on a fixture or a fresh seed.

Re-projecting the live seeded store — 37 constraints derived from 69 observations, written by a
build that predated every judgement improvement since — inverted the necessity distribution:

    frozen (schema v0)                reprojected (schema v2)
    must 36, should 1                 should 34, must 3
    proposed 37                       proposed 37
    3 of 37 scoped                    4 of 37 scoped

    34 changed · 3 unchanged · 0 skipped · 67s · migrated v0 -> v2 on open

The three it held as hard boundaries were **Commute duration**, **Work cutoff time** and
**Market opening hours** — one physical constraint and two facts about the outside world. Every
rule it flexed is a preference: *Sci-Fi Reading before bed*, *Oats Timing*, *Maximum Deep Work
blocks*, *Timeboxing Preference*.

Three claims are settled by that run at once, and none of them could be settled by a fresh seed:

- **I4 pays off.** These constraints were created before the necessity judgement existed. Under
  the old fold path they would have kept `MUST` forever no matter how many times Hugo restated
  them, because a fold refreshed one timestamp and nothing else. 34 of 37 acquiring a corrected
  field is the invariant doing the thing it was specified for.
- **The schema ladder handles a store older than the code.** The store opened at `user_version`
  0 and migrated to 2 without intervention — the case that had never been exercised, because
  every prior run re-seeded from scratch.
- **`necessity` discriminates.** It was `MUST` on 36 of 37 because it derived from
  `is_declaration`, which answers *was this stated outright*. Asking *what breaks* instead
  separates a corpus that a consumer previously could not filter at all.

Run conditions: `OpenRouterJudge` on `google/gemini-3.6-flash`, `reasoning.effort: minimal`, two
questions re-asked per observation (`tier`, `necessity`) at concurrency 8. Executed against a
**copy** of the store — the frozen original is retained, because re-projection rewrites derived
state in place and the before-state is the exhibit.


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
- **The clearest live illustration of why I1 exists**, found in this repo at
  `graphiti_constraint_memory.py:94-138`. The constraint-retrieval relevance function is
  hand-rolled bag-of-words: `score = sum(1 for term in query_terms if term in blob)`, where
  `query_terms` is the query lowercased and split, and `blob` includes `json.dumps(episode)` —
  so terms also match field names, statuses and uuids, making non-zero scores noise.

  Zero-score episodes are **not** filtered; every episode is appended, then sorted by
  `(-score, updated_at)` **ascending** and truncated. `-score` ascending is correct — best
  first — but the `updated_at` tiebreak is ascending over ISO strings, so among equal scores
  the order is **oldest first**.

  Consequence: whenever the query's literal words are absent from the episode text — which,
  per the `gym` case above, is the common case — the ranker silently returns the *oldest* N
  constraints and truncates away everything current. Not "returns nothing", and not "falls
  back to recency": it falls back to **reverse-chronological**. The longer the store runs, the
  more reliably it surfaces the least relevant rows, and nothing raises.

  (This was initially suspected as the cause of #114, "Graphiti returns 0 durable
  constraints". It is not — the function never returns empty. #114 should be debugged upstream
  at `get_episodes` or a filter above it.)

## Known gaps in the built code

### Closed

**Provenance links were add-only (I4).** `link_observation` inserted and never deleted, so a
re-projection that dropped an observation left the stale link behind and over-reported the
evidence counts promotion and decay rely on. Closed by `ConstraintStore.replace_links`, which
deletes then inserts inside one transaction so a concurrent reader never sees a constraint with
no provenance at all.

**Duplicate constraints under concurrent projection (I5).** Two projections of the same rule
could each snapshot a candidate list lacking the other's constraint, each be told "this is new",
and each create a row — duplication in the layer whose whole job is canonicalisation. The race
window spans a full model round-trip, so it was wide. Closed by serialising the read-judge-write
span with one lock per constraint store.

### Open — `upsert` is destructive to provenance links

`ConstraintStore.upsert` calls `replace_links(uid, constraint.source_observation_uids)`. That
was the fix that closed I4 — re-projection must be able to *drop* an observation, not only add
one — and it is correct for re-projection. It also makes `upsert` **destructive to any link not
present on the object handed to it**.

Consequence: **upserting a stale `Constraint` silently destroys links added since it was read.**
This has now bitten twice, both times in the fold path, where `existing` comes from a
pre-fold `durable()` snapshot and `link_observation` has run in between. Both times the symptom
was a lost provenance link with nothing raised; the second time it broke an existing test, which
is the only reason it was caught.

The current mitigation is a re-read immediately after `link_observation`, which works but leaves
the trap armed for the next caller. The real fix is to make the destructive behaviour opt-in
rather than default — `upsert` leaves links alone, and re-projection calls `replace_links`
explicitly — so that the dangerous operation is the one you have to ask for.

Worth noting the shape of the mistake: closing I4 made one operation authoritative over
provenance, and nothing was done to stop *other* callers of that operation from inheriting the
authority. A fix that widens a method's power needs to narrow who may use it.

### Open — carried from review, not yet fixed

**Typing is documented, not enforced.** There is no `src/memory/py.typed`, so mypy treats the
package as an untyped library and degrades cross-module references to `Any`. The enums on
`ConstraintView` and the `ConstraintLike` protocol are therefore documentation until that file
exists — a wrong type crossing the process seam would not be caught.

**Two comments overstate what the code does.** `projection` claims skipping the session tier
"bounds the candidate list so the write-path prompt cannot grow without limit". It does not:
`durable()` has no `LIMIT`, and durable constraints grow without bound — the legacy store held
~606 concepts. Every canonicalise call serialises all of them into a prompt, now inside a
per-store lock. Likewise `ingest` binds `store.by_session(...)` to a variable named `recent`
when it is every observation in the session, unbounded, fed straight into the dedup prompt.
Both need real windows.

**Session-tier constraints are written and never removed**, despite the design saying the
session tier "dies at session end". They are also never read — `get_active_constraints` returns
durable only — so their sole current effect is to exist.

**The fold branch drops the assertion signal.** When a later declaration restates an existing
`SHOULD` rule, `project` links the observation and returns without upgrading `necessity` to
`MUST`. The assertion promotion path is therefore lost on every restatement.

**Neither store exposes `close()`**, both default to `check_same_thread=True`, and `project`
does synchronous sqlite I/O on the event loop. An MCP server dispatching to a thread pool would
raise.

### Open — concurrency

**Serialisation is per process and per store instance.** The lock closing the
duplicate-creation race is mutual exclusion over one `ConstraintStore` object,
weak-keyed so a collected store does not leak its lock. Two store instances on
the same file, or two processes, can still both create a constraint for one
rule: there is no database-level uniqueness. For a design whose stated shape is
a standalone server, that is a real limit rather than a closed gap.

**Per-session serialisation is enforced for projection but assumed everywhere else.** The lock
covers `project`. Nothing prevents a future caller from driving `ingest` concurrently over the
same session in a way the design has not been reasoned about.

### Open — fields that advertise a discriminator and carry no signal

**This spec describes a richer information model than the pipeline implements, and every gap
is silent.** A consumer reading `models.py` sees a field, builds a filter on it, and gets all
or nothing — with no test failing, because no fixture uses a value the pipeline can produce.
Verified against the code on 2026-08-19:

| field | state | why it is not merely unfinished |
|---|---|---|
| `Reliability` | **never wired.** Defined in `models.py`, exported from `__init__`, read by nothing — not stored, not weighted, not consulted, not even in a test | It is the field that should bound the #168 evidence-inflation harm: three retries are three `UNEXAMINED` rows, not three confirmations. Without it the gate has no notion of confirmation at all |
| `is_declaration` | **was newly orphaned; deleted in `40e041f`.** Judged, carried on `IngestResult`, plumbed through the prompt and the `Judge` protocol — and consumed by nothing from #156, which gave `necessity` its own judgement and removed its only reader, until it was found four commits later | Kept in this table because the *shape* outlived the field. A reader saw a value carefully computed and threaded through four modules and reasonably concluded it mattered; the fix that killed it left the producer running |
| `Status` | **unreachable value.** `projection.py` hardcodes `Status.PROPOSED` on both branches, so `LOCKED` is never emitted | Anything filtering for locked rules gets an empty set, which is indistinguishable from "nothing is locked yet" |
| `frame_slot` | **null on 94%** (1,562 of 1,662; 75% within PROFILE), where null means three incompatible things | See Open questions. Nothing can branch on it correctly until the meanings are separated |

`channel` is **not** in this list, though a sweep finding about judges not receiving it invites
the confusion: it is genuinely consumed, driving `source` via `_SOURCE_BY_CHANNEL` in both
`projection` and `reprojection`.

The two failure shapes are worth separating, because they need different fixes. **Never wired**
is unfinished work. **Newly orphaned** is a regression artifact — a fix removed the only
consumer and left the producer running, so the code actively performs work whose result is
discarded, and looks intentional to anyone reading it fresh. The second shape has no natural
detector: nothing fails, and the field's presence is its own argument for keeping it.

### Open — a merge is irreversible, and L2 has no inverse

**I2 protects the evidence and nothing protects the derivation.** L1 is append-only, so the
observations behind a wrong merge all survive — the material needed to undo it is right there.
But **no operation splits a constraint.** `project()` merges, `reproject()` re-derives from
whatever provenance already says, and provenance is the input to that fold rather than
something it can revise. A constraint that swallowed an observation it should not have keeps it
forever, and re-projection faithfully re-derives the wrong thing every time it runs.

This is not hypothetical and the repair has already been done by hand. `Lunch: Lunch break` was
merged into `Daily Meals: include breakfast, lunch and dinner every day` (#169, a part read as a
restatement of its whole). Undoing it meant deleting a row from `constraint_observations` with
raw SQL, because the API has no verb for it. **The one corrective operation the store needed
most had to be performed underneath the store.**

The asymmetry is the finding: a wrong merge is *permanent in L2 while being fully recoverable
from L1*. That is I2 doing exactly its job and L2 having no counterpart to it. Everything the
design says about derived state being cheap to rebuild is true only for fields — the
*partition* of observations into constraints is derived state that nothing can re-derive.

What is needed is a `split(constraint_uid, observation_uids) -> (uid, uid)` that mints a new
constraint, moves the named provenance links, and re-projects both. Minting rather than
reusing, per I3. Whether the *decision* to split is a judgement the system may make on its own
or one that belongs at stage 6 of the gate is the open half — #169 shows the model can now tell
a part from a whole prospectively, which is not the same as trusting it to revise a merge it
already made.

### Open — a shared idempotency key with divergent payloads

Closed for the common case by #168, which gave the write a caller-supplied identity so a retry
is a no-op rather than a blind replay (I5, applied to L1 appends for the first time). One path
survives and is asserted rather than fixed: **same key, different text**, from a mangled retry
or a caller reusing a key. `append` is a no-op on a known uid so L1 keeps the original, but
projection would run on the incoming text — yielding a constraint that describes a statement
existing nowhere in the observation log, with provenance pointing at a row that says something
else. Re-projection would later "correct" it, so the store appears to change its mind
unprompted. **First payload wins.** Found by mutation testing, and reachable only where two
failure modes meet, which is why two earlier tests passed against the bug.

## Scope — one server, several projections (#159)

Whether this serves the planner or every agent. **One L1 with several L2 projections, not
several servers.**

**L1 already generalises.** An observation is text, channel, provenance, a minted uid, a
timestamp and anchors — nothing constraint-shaped. L2 is where the constraint assumption lives.

**Sharing L1 is safe because the contamination guard is already built and enforced.**
`Provenance.GENERATED` exists so a rule emitting a calendar block cannot observe its own output
as evidence, and `ingest` rejects anything that is not `OBSERVED`. System-authored records — an
admonishment log among them — carry `GENERATED` and inherit that guard. This is the strongest
argument for one server, and it is load-bearing today rather than aspirational.

**Decay must not be shared, and this is the sharp edge.** A preference decays because relevance
fades. *"The system nagged Hugo on Tuesday"* is a fact about history and is permanently true.
Apply a half-life to it and the system forgets it nagged, then nags again — and the failure is
**self-concealing**, not merely silent: a forgotten nag is indistinguishable from a first nag,
the re-nag is logged, and the log then looks correct. **Each projection declares its own decay
contract. The admonishment projection declares none.**

**Admonishment state folds, so I2 holds.** Escalation is `count(nag events for a subject)` and
a function of elapsed time — nothing mutable. The mutable state hides *in flight*, between
deciding to nag and confirming delivery. Log only the decision and the ladder runs ahead of
what the user actually experienced; log only the delivery and something must hold the decision
across the gap — and that something is a mutable `pending` field wearing a memory costume. Two
events, intent and outcome, folded. This is the same shape as `ingest` committing before
`project` runs, which leaks an orphan observation per retry and **is still live**.

**Acknowledgement is the hard fold, and it crosses provenance.** *"Stop nagging me about X"* is
an `OBSERVED` user statement that must suppress nags derived from the system's own `GENERATED`
log. So admonishment state is a fold over the union of both, latest-wins (AGM success
postulate; see Gate). The design consequence is an asymmetry: **the admonishment projection
reads both provenance classes, while the constraint projection reads only `OBSERVED`.**

**Unsettled, and it needs settling before #140:** whether admonishment subjects share the
constraint anchor space or get a disjoint one. Shared means a nag about `sleep` traverses to
the bedtime rule — plausibly the feature, since it is how a nag would know *why* it is nagging,
and plausibly a collision. Cheap to decide now, expensive once the taxonomy has edges. It
belongs on the map as a decision, not as an emergent property of whichever code lands first.

## Open questions (fog)

- Re-projection mechanics when L3 changes — incremental or full rebuild, and what invalidates.
- ~~How conditionality is encoded~~ — **closed**: #137 shipped path intersection.
- Whether admonishment subjects share the constraint anchor space. See Scope; blocks #140.
- Migration of the 1,662 existing rows. Note that **1,455 of them are SESSION-scoped** — 88%
  of that table is per-thread chatter, not preference, and a migration treating it as a
  preference store inherits all of it. And `frame_slot` is null on 1,562 of 1,662 (94%; 75%
  even within PROFILE), where null means three incompatible things — genuinely unanchored,
  extraction failed, or legacy row. Nothing can branch on null while it means all three; the
  view needs an explicit discriminator, and classifying the legacy rows is a meaning judgement,
  so one offline `:batch` pass.
- Whether Notion survives as a human-editable view onto the graph.
- Multi-user, or single-tenant forever.
- Tuning the loop-2 induction prompt itself.

## Out of scope

Rewriting the timeboxing agent (map C). Patcher and calendar sync (map A). The task↔block half
of #118 (map A). Outcome-event capture ships on map A as #123 against the existing patcher.
