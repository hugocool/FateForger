# Required blocks: the planning session is always on the plan, and the haunt knows when it is not

Decided with Hugo on 2026-09-03/04 in a grilling on #209 followed by a brainstorm, with a
blindspot pass on the kind-identity choice and one on the memory side, and a second take from
the session that owns the haunt's minted-mark work (#221). Supersedes the option analysis in
`2026-08-30-required-blocks-design.md`, which stays as the record of what was compared.

## The ask

On every working day the plan contains a block of kind `planning`: the session in which
tomorrow is timeboxed. The planner places it; its id is registered; something watches, and if
the block vanishes or leaves its bounds the haunt starts. The same mechanism must carry any
other habit Hugo later decides is required (from a review session, say), and must be built so
that *placement* rules can be added beside *presence* later without a new mechanism.

## Decisions

1. **Existence plus enforcement, not just placement.** The rule says a block of a kind must
   exist; the planner places it; a watcher verifies and haunts. (Hugo)
2. **What starts the haunt:** the block is gone, or it has left its bounds — moved to another
   day, or ending after the sleep boundary from memory's day frame. Moves inside the day are
   silent. (Hugo)
3. **Poll first, webhook later.** The watcher runs on the haunt's existing reconcile tick. Push
   notifications are #289, behind the same "calendar changed" interface. (Hugo)
4. **The registered id is a cache, not the truth.** Fast path is fetch-by-id; on a miss the day
   is listed once and presence is re-derived from the mark tmbx writes; a found block is
   re-registered silently. The register may be wrong for one tick and no longer. (Hugo)
5. **Memory learns to say "must exist" first** (#212); the planning session is the first rule
   that says it. No hardcoded interim requirement. (Hugo, over the host-supplied-set shortcut)
6. **Kind identity is B′:** a registry of *enforceable kinds* owned by memory, each a human
   slug word linked to an anchor, created only by an explicit promotion that asks first. Never
   minted by an observation. tmbx writes the slug verbatim into `tmbx.slug`; presence is slug
   equality. Rejected: a closed enum (deploy per habit) and the anchor uid as slug (anchor
   drift with no merge gate until #140, unreadable in the calendar, scalar slug). (Hugo, after
   the blindspot pass and a2's take)
7. **Instance id and kind are two fields.** The `ffplanning…` id stays the create-side
   idempotency and adoption key for the event the nudge books; `tmbx.slug` carries the kind.
   Neither is folded into the other. The block carries no anchor uid; the registry row is the
   join between slug and anchor. (a2's condition, accepted)
8. **A hand-booked block without a slug is not adopted; the haunt nags.** Adopt-by-asking
   (a closed-set question over the day's unslugged events) is a later increment. (Hugo)
9. **Presence with bounds, not placement.** "At the end of the workday" stays prose the
   planner reads. The watcher is built as predicates over slugged blocks (`present`,
   `within_bounds`) so `before(kind)` and friends are added as predicates later. (Hugo)
10. `requires_block` is **durable-only**; a session-tier statement about tomorrow's session is
    a fact for the planner, not a standing requirement. Required and `necessity` are
    independent: required governs presence, necessity governs how firmly the planner treats
    placement.

## Increments

Three, each shippable alone, in this order:

1. **Memory can say it** — registry, promotion, the `requires_block` field, `day_types`.
2. **The planner must place it** — one kernel requirement over the day's required kinds; slug
   visible on read.
3. **The watcher enforces it** — `RequiredBlockRule` in the haunt, register as cache.

A spike (#210) precedes all three: do tmbx's private extended properties survive a drag, a
rename, a resize and a duplicate in the Google Calendar UI? Decision 4 rests on yes. If no, the
miss path cannot re-derive and the register becomes the truth, which changes §5 and must be
re-decided before §5 is built.

## 1. Memory side

### Registry of enforceable kinds

New table, schema version 4:

```
enforceable_kinds (
  slug                 TEXT PRIMARY KEY,   -- lowercase identifier, validated by shape
  anchor_uid           TEXT NOT NULL,      -- the anchor this kind is about
  rule_observation_uid TEXT NOT NULL,      -- the rule text stated at promotion
  created_at           TEXT NOT NULL
)
```

Written only by `MemoryService.promote_kind(slug, anchor_name, rule_text)`, exposed as the MCP
tool `memory_promote_kind`. The host asks first; the server writes once. The call, **in this
order**:

- validates `slug` as an identifier (lowercase letters and hyphens) and refuses a slug already
  registered (`DuplicateKind`);
- resolves `anchor_name` through `resolve_anchors` so it prefers an existing anchor over a
  near-duplicate (#291), minting one only if the judge says it is new;
- appends `rule_text` as a durable observation through the ordinary `observe` path, under a
  `write_uid` of `promotion-<slug>`, so the requirement exists as a rule with provenance and
  reaches the planner like any other. A suppressed rule fails the promotion;
- **then** records the registry row, with `rule_observation_uid` that same write uid;
- re-derives that one constraint through `reproject(uid=…, apply=True)` with the new kind now
  among the offered slugs, and reads it back. If `requires_block` is not the new slug the
  promotion has failed — nothing would ever require a block of this kind — so the row is
  removed and the call raises. The rule stays: it is a durable statement the user made and L1
  is append-only (I2).

The order is the design, not an implementation detail. Registering first opens a window in
which a kind row exists that no rule states, and two things fall into it: a concurrent
`observe` is offered a slug this call is about to roll back — and stores a constraint naming a
kind the registry no longer holds, indistinguishable from one someone removed on purpose — and
the compensating delete becomes the only thing keeping an unstated kind out of the store.
Storing the rule first closes it, at the cost of the rule being judged before the kind exists;
the re-derivation above pays that cost, and the write uid makes the gap between the two writes
retryable rather than a source of duplicate observations.

The first promotion is `planning`, anchor "planning session", rule "Every working day has a
planning session in which the next day is timeboxed." Hugo runs it once, by hand, from a Claude
session or the CLI. A Slack surface for promotion is increment B of #266.

`frame_slot` is not reused. It stays as it is and is marked dead in its docstring; a later
ticket removes it.

### `Constraint.requires_block`

`Constraint.requires_block: str | None` and `ConstraintView.requires_block: str | None`,
durable-only. Projected from a **sixth judgement**, `requires_block(observation, kinds)`,
where `kinds` is the registry's current slugs. The prompt asks whether the statement says a
block of some kind must be present on the day and, if so, which of the listed kinds; the
answer is `null` or one of them. The returned slug is verified against `kinds` before it is
stored, exactly as `dedup` verifies `duplicate_of`; an unknown slug is a `ValueError`, never a
stored value.

The judgement runs in the ingest `gather` beside the other five, and in reprojection's
`_judge_all` beside `tier` and `necessity`, so `reproject()` carries the field onto existing
rows. It is a derived field (I2); the observation log is untouched.

### `day_types` gets a writer

The tier question gains `day_types`: the list of day types the rule is limited to
(`working`, `weekend`, `vacation`, `holiday`, `sick`), empty when the statement does not scope
it. `Applicability.day_types` already has its reader (`applies_on`); this gives it a writer.
Reprojection carries it.

### Read path

Unchanged in kind. `get_active_constraints(day, day_type)` returns rules with
`requires_block` set like any other field. The day's required-kind set is
`{c.requires_block for c in active if c.requires_block}` — set construction over stored
values, no registry read, no model. The AST guard on `read_api.py` holds.

### Evals (real model, eight draws per case, rate asserted)

- **Positive:** the promotion's own rule text; the end-of-day closure rule. Must map to
  `planning` in ≥ 7 of 8.
- **Negative:** a duration cap, a timing relation, an alternation rule, a guardrail. Must map to
  `null` in ≥ 7 of 8.
- **Ambiguous, recorded not asserted:** "I timebox my day by allocating fixed blocks for tasks
  and activities." Its rate is written into the test's docstring so a later prompt change can
  see whether it moved.
- **`day_types`:** "on working days" → `["working"]`; "every day" → `[]`; "when I'm on
  holiday" → `["vacation"]`.

Unit tests stub the model and assert the plumbing: candidates passed, verified slug stored,
unknown slug refused, field carried by reprojection, promotion refuses a duplicate slug and a
malformed one.

## 2. Planner and kernel side

### The required set becomes a fact

At candidate time the host's context port (`timeboxing_host.py`) already fetches the day's
active constraints. It additionally files one typed fact:

```
FactKind.REQUIRED_BLOCKS  value = {"slugs": ["planning"], "by_rule": {"planning": "<rule uid>"}}
```

derived from the rules' `requires_block` values. Arithmetic. Skeleton time is untouched: the
kernel's context port still returns an empty context for any target but the candidate, so
stage 3 does not read the calendar.

### One catalog entry

`candidate.required_blocks` — `target_artifact=VALIDATED_CANDIDATE`, `owner=PLANNER`,
`resolution="assume"`, `hard=True`. Readiness runs before the candidate exists, so the entry
has two halves:

- **Before the planner runs**, the gap is *open* whenever the `REQUIRED_BLOCKS` fact lists at
  least one slug, and *satisfied* when it lists none. Open means the planner owns a placement
  this turn; it is told which kinds in the brief and may record its placement as an assumption
  against this requirement. It cannot ask about it (`illegal_user_blocker`).
- **After the planner submits**, the result is checked: every slug in the fact must appear on a
  block of the candidate — an `add` or `update` op carrying that `slug`, or an event already on
  the day carrying it in the snapshot. A miss is the `required_block_missing` refusal below.

Both halves are computed over the set; the catalog does not grow when a second kind is tracked.
`skeleton.gym_placement` is left alone in this increment and retired into this entry later.

### The brief

For each required slug, one sentence in the obligation text: "A `planning` block is required
today (from memory: <rule name>). Set `slug: planning` on it verbatim. Place it from the day's
other rules and attribute the time as assumed." Existence is `(from memory: …)`, time is
`(assumed: …)`: two claims about one block, stated as two.

### Refusals

- The planner cannot turn the requirement into a question: `illegal_user_blocker` already
  refuses a blocker naming a planner-owned requirement. Infeasibility stays a typed blocker
  naming the conflict, with `BlockerOption`s where the answer set is closed.
- **A candidate missing a required slug is refused at submit**, as a skeleton with the wrong
  payload is (#267): result error `required_block_missing` naming the slug; the planner sees it
  and retries; nothing is stored. A planner that wrote the block and forgot the slug is caught
  here, not at 23:00 by the watcher.

## 3. tmbx side

- **Write path unchanged.** `slug` exists on the `add` and `update` ops and is written to
  `extendedProperties.private["tmbx.slug"]` on every commit; the journal records it.
- **Read path wakes up (#211).** `plan_read` renders `slug` in the handle table so the planner
  sees a block already carries `planning` and does not add a second.
- **Shape only.** tmbx validates a slug as a lowercase identifier and knows nothing about the
  registry; membership is the kernel's check. Two blocks of one kind on one day is legal and
  reported as a count, never resolved by picking one.

## 4. The watcher

`RequiredBlockRule`, beside `PlanningSessionRule` in `haunt/reconcile.py`, evaluated on the
same tick per user.

**Inputs.** The day's required slugs from memory's read path with `day_type`. The register
`planning_session_refs`, re-keyed to `(user, date, slug) → event_id`. Bounds: the day, and the
sleep time from the same frame rule the session uses.

**Predicates**, over the slugged blocks of the day, so more can be added:

- `present(slug)` — some event carries `tmbx.slug == slug`; for `planning` an event whose id
  starts with `ffplanning` also counts, so a session the nudge itself booked is seen.
- `within_bounds(event)` — starts on the day, ends before the sleep boundary.

**Evaluation.**

1. Fetch the registered event by id. Present and within bounds → satisfied, no list.
2. Miss → list the day once. Found in bounds → re-register silently, satisfied. Found out of
   bounds → haunt, reason `moved_out`. Not found → haunt, reason `missing`.
3. **Either read fails → no verdict.** Logged with the error type and message; the current
   haunt state is left exactly as it was; nothing new fires and nothing clears. Absence of a
   read is not absence of a block (#226).

**Haunting is the existing machinery.** Nudge schedule, backoff and the planning card are
unchanged; the rule decides when they fire and names the reason. No haunting while a session
is open for that user, which the reconciler already honours via `standing_for`.

**Cache write.** After a commit whose ops include a slugged block, the host registers the
event id from the commit result under `(user, date, slug)`. Best-effort: a missed write costs
one calendar list on the next tick.

## 5. Errors

Named, never swallowed:

| code | class | where | meaning |
|---|---|---|---|
| `unknown_kind` | `memory.judge.UnknownKind` | promotion, judgement | a slug not in the registry |
| `duplicate_kind` | `memory.kind_store.DuplicateKind` | promotion | slug already registered |
| `required_blocks_unreadable` | — | watcher | memory could not be read; no verdict |
| `calendar_unreadable` | — | watcher | fetch or list failed; no verdict |
| `required_block_missing` | — | kernel submit | candidate lacks a required slug; names it |

Both classes are `ValueError` subclasses carrying `code` as a class attribute, and both prefix
their message with it (`"[duplicate_kind] kind 'planning' is already registered"`) because the
MCP tool surfaces the message as-is.

## 6. Testing

- **Unit, model stubbed:** the sixth judgement's plumbing; reprojection carries
  `requires_block` and `day_types`; promotion's refusals; kernel requirement satisfied and
  unsatisfied over ops and over snapshot; submit refusal names the slug; watcher states
  present / moved out / missing / stale register re-derived / failed read leaves state, with a
  fake calendar client.
- **Eval, real model, eight draws:** §1 evals.
- **Spike, first:** #210 against Hugo's calendar, result recorded in this document's
  "Increments" section before §4 is built.
- **End to end:** commit a plan with a `planning` block, drag it to the next day in Google
  Calendar, see the haunt fire on the next tick with reason `moved_out`.

## 7. Out of scope, deliberately

- Push notifications (#289).
- Adopting a hand-booked block by asking (paper §7).
- Placement predicates (`before(kind)`, `after(kind)`) — more predicates in §4, later.
- Teaching the planner `bn` for "at the end of the workday" (#215).
- A Slack surface for `promote_kind` (increment B of #266).
- Retiring `skeleton.gym_placement` into `candidate.required_blocks`.
- Removing `frame_slot`.

## 8. Sequence and tickets

| step | what | ticket |
|---|---|---|
| 0 | Spike: private properties survive a Google UI edit | #210 |
| 1 | Memory: registry, `promote_kind`, sixth judgement, `day_types`, schema v4, reprojection, evals; promote `planning` by hand | #212 |
| 2 | tmbx: render `slug` on read | #211 |
| 3 | Kernel: `REQUIRED_BLOCKS` fact, `candidate.required_blocks`, brief text, submit refusal | #214 |
| 4 | Haunt: `RequiredBlockRule`, re-keyed register, cache write after commit | #213 |

#209 is closed by this document. #128's "one registry, memory owns it" is what §1 builds.
