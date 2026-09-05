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

**Spike result (2026-09-05, calendar `hugo.evers@gmail.com`, probe event
`05fh8qg0fj0r9k2kvvj6fhj4dg` on 2030-01-07):** a probe written by tmbx's own adapter with
`tmbx.uid`, `tmbx.slug=planning` and `tmbx.type=PR` was moved (10:00 → 14:00), renamed and
resized (30 → 45 min) in one PATCH through a *different* client (the claude.ai Google Calendar
connector), then read back through the tmbx adapter: **all three keys survived.** Decision 4
stands for edits made through the API by any client. Still to observe by hand in the Google
Calendar web UI: a drag, and the "Duplicate" action (whether the copy carries the slug, which the
watcher must then count rather than resolve). Script:
`scripts/spikes/private_props_survive_ui_edit.py`.

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

- **Positive:** the promotion's own rule text. Must map to `planning` in ≥ 7 of 8.
- **Negative:** a duration cap, a timing relation, an alternation rule, a guardrail. Must map to
  `null` in ≥ 7 of 8.
- **Recorded, not asserted:** "I timebox my day by allocating fixed blocks for tasks and
  activities" — it says he timeboxes, not that a session must be on the plan. And the end-of-day
  closure rule ("reserve 15-20 minutes at the end of the workday to update artifact links and
  board status"), which this document originally listed as a `planning` positive and which is
  not one: unhandled, the model answers `null` 8 times out of 8, because updating links and
  board status is administrative closure rather than deciding how tomorrow's time is spent. It
  held up as a positive only while the prompt carried a paraphrase of it, which made the eval
  measure recognition of a handed answer. It is a candidate second kind (see §7), not a question
  the sixth judgement is failing. Both rates are written into the test's docstring so a later
  prompt change can see whether they moved.
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

The fact is filed on **every** successful candidate resolve, carrying `{"slugs": [], "by_rule":
{}}` when no rule requires a kind. Facts merge by `fact_id` and are never deleted, so a host
that filed this only when something was required would leave the previous turn's slugs standing:
suspend the rule mid-session and every later candidate is still refused for a block nothing asks
for any more, with nothing inside the session able to clear it. The empty value under the same
id is that clearing, and it is satisfied by construction everywhere it is read.

`by_rule` names one rule per slug. Two rules can require one kind and memory promises no order,
so the lowest uid wins — arbitrary, but stable, so the brief does not tell the user a different
rule was asking on each turn.

### One catalog entry

`candidate.required_blocks` — `target_artifact=VALIDATED_CANDIDATE`, `owner=PLANNER`,
`resolution="assume"`, `hard=True`. Readiness runs before the candidate exists, so the entry
has two halves:

- **Before the planner runs**, the gap is *open* whenever the `REQUIRED_BLOCKS` fact lists at
  least one slug, and *satisfied* when it lists none. Presence of the fact is the wrong test —
  it is always present, and what it lists is the question. Open means the planner owns a
  placement this turn; it is told which kinds in the brief and may record its placement as an
  assumption against this requirement. It cannot ask about it (`illegal_user_blocker`).
- **After the planner submits**, the result is checked: every slug in the fact must appear on a
  block of the candidate. Presence is read from the captured `plan_apply` — the post-patch
  `rows`, which are the day as it will stand and the only place a block already on the calendar
  shows up, plus the `add` and `update` ops as the fallback for a capture with no rows. Never
  from the artifact: a model-written claim of presence is a forged basis. A miss is the
  `required_block_missing` refusal below.

  A capture that says nothing — missing, empty, unparseable, or carrying neither rows nor a
  patch — is **not** a missing block. Refusing there would name the one cause that is certainly
  not the case and send the planner to add a block the plan may already hold, while the real
  failure goes unsaid; `CandidateNotApplied` and the unapplied-candidate guard own it and name
  it. With a real capture in hand this never fails open.

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
  payload is (#267): `PlanningResultRefused`, message prefixed `[required_block_missing]` and
  naming the slug; the planner sees it and retries; nothing is stored. A planner that wrote the
  block and forgot the slug is caught here, not at 23:00 by the watcher. The kernel repeats the
  same set arithmetic when it accepts the draft, returning `TurnFailed(code=
  "required_block_missing")`, so a host that publishes no required-slug file still cannot show
  the user a candidate that is missing one.

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

**Inputs.** The day's required slugs from memory's read path with `day_type` — the locked
`day_type` of the user's newest open or committed session for that day (`day_type_for`), else
the weekday-derived one. Weekday arithmetic knows only working and weekend, so a Tuesday of
annual leave would otherwise be asked for under the wrong day type and every `vacation`-scoped
rule would go unseen. The read is capped at 200 rows and takes no cursor; a full page is logged
as `required_blocks_truncated`, because a required kind sitting past the cap is one the watcher
will never look for.

The register is an in-process cache on the rule, `(user, day, slug) → event_id`, rebuilt from
the calendar on any miss and lost on restart; a persistent register would need a migration and
buys nothing the miss path does not (one list per rebuild). Entries for days other than the one
being evaluated are dropped at the start of each tick.

Bounds: the day, and the `sleep` of the user's session's `DAY_FRAME` fact for that day
(`day_frame_for`), else end of day; a sleep time before 04:00 is after midnight and lands on the
next day.

**Predicates**, over the slugged blocks of the day, so more can be added:

- `present(slug)` — some event carries `tmbx.slug == slug`; for `planning` an event whose id
  starts with `ffplanning` also counts, so a session the nudge itself booked is seen.
- `within_bounds(event)` — starts on the day, ends before the sleep boundary.

**Evaluation.**

1. Fetch the registered event by id. Still of the kind and within bounds → satisfied, no list.
   Still of the kind and **out** of bounds → the id resolves, so the block was dragged to
   another day or pushed past sleep — but the user may instead have fixed the day by booking a
   NEW block of the kind rather than dragging the old one back, so the day is listed once for a
   replacement. An in-bounds block of the kind → re-register its id, satisfied. None found →
   haunt, reason `moved_out`, cache left as is (a bare "no list" here would leave the haunt
   sticky against exactly that repair).
2. Miss — the id is gone, or the event it resolves no longer carries the kind → list the day
   once. Found in bounds → re-register silently, satisfied. Found out of bounds → haunt, reason
   `moved_out`. Not found → haunt, reason `missing`.
3. **Any read fails → no verdict.** The listing, the fetch, the day frame, the day type, or an
   event in a shape that will not parse. Logged with the error type and message
   (`calendar_unreadable`, `required_blocks_unreadable`); the current haunt state is left
   exactly as it was; nothing new fires and **nothing is pruned**. Absence of a read is not
   absence of a block (#226). `evaluate` returns `RequiredBlockOutcome(jobs, undecided)`, where
   `undecided` names the job-id prefixes the reconciler must leave alone —
   `rule:required_blocks:<scope>:<day>:<slug>:` for one slug it could not judge, and the whole
   `rule:required_blocks:<scope>:` when even the required set could not be read.

**The `planning` kind is haunted only for `moved_out`.** Whether the planning session is on the
calendar at all is `PlanningSessionRule`'s business — it already nudges, books the card and
starts the session — so a `missing` verdict for `planning` schedules nothing. The two ladders
are disjoint by construction rather than by a check, and double-nudging is impossible. Every
other slug haunts on both reasons. `moved_out` is the half only the watcher can see: the block
exists, so the planning ladder is satisfied, and nothing else notices it drifted.

**Haunting is the existing machinery.** Nudge schedule and backoff are unchanged; the rule
decides when they fire and names the reason. No haunting while a session is open for that user,
which the reconciler already honours via `standing_for`. A required-block reminder is **one DM
line** for every kind, `planning` included — never the planning card, which books a planning
block, and a `moved_out` one already exists. Three escalating lines per reason; past the third
rung the last repeats.

**Reminders are revalidated before they post.** A scheduled rung is a claim about the calendar
as it stood when the tick ran, up to eight hours earlier on the last rung of the backoff. The
dispatcher calls `rule.recheck(user_id, slug, now)` — the same predicates over the same cache —
and drops the reminder unless the verdict still equals the reason it was scheduled for. No
verdict is not a confirmation, so that drops too. The rule reaches the dispatcher as
`runtime.required_block_rule`; a runtime without it drops required-block reminders and logs it.

**Cadence.** `PlanningGuardian.schedule_interval` reconciles every 15 minutes
(`planning_guardian:interval_reconcile`, coalescing, `max_instances=1`), configurable by
`FF_RECONCILE_INTERVAL_MINUTES` (0 disables), beside the daily job. The watcher sees a block
leave the plan only on a tick, so this is the upper bound on how long the drag goes unnoticed.

**No cache write on commit.** The commit does not register the event id; the first tick after a
commit lists once and registers what it finds.

## 5. Errors

Named, never swallowed:

| code | class | where | meaning |
|---|---|---|---|
| `unknown_kind` | `memory.judge.UnknownKind` | promotion, judgement | a slug not in the registry |
| `duplicate_kind` | `memory.kind_store.DuplicateKind` | promotion | slug already registered |
| `required_blocks_unreadable` | — | watcher | memory could not be read; no verdict |
| `calendar_unreadable` | — | watcher | fetch or list failed; no verdict |
| `required_block_missing` | `slack_bot.PlanningResultRefused` / `TurnFailed.code` | submit tool, kernel acceptance | candidate lacks a required slug; names it |

Both memory classes are `ValueError` subclasses carrying `code` as a class attribute, and both
prefix their message with it (`"[duplicate_kind] kind 'planning' is already registered"`)
because the MCP tool surfaces the message as-is. The submit refusal does the same
(`"[required_block_missing] this candidate has no block of a kind the day requires: …"`), so the
planner sees one name for the condition at both refusal sites.

## 6. Testing

- **Unit, model stubbed:** the sixth judgement's plumbing; reprojection carries
  `requires_block` and `day_types`; promotion's refusals; kernel requirement satisfied and
  unsatisfied over ops and over snapshot; submit refusal names the slug; watcher states
  present / moved out / missing / stale register re-derived / failed read leaves state, with a
  fake calendar client.
- **Eval, real model, eight draws:** §1 evals.
- **Spike, first:** #210 against Hugo's calendar, result recorded in this document's
  "Increments" section before §4 is built.
- **End to end:** commit a plan with a `planning` block, then in Google Calendar drag it to
  tomorrow → the haunt fires with reason `moved_out` within 15 minutes; delete it → the
  *planning ladder's* nudge, not the watcher's, because a `planning` block that is simply gone
  is `PlanningSessionRule`'s to notice.

## 7. Out of scope, deliberately

- Push notifications (#289).
- Adopting a hand-booked block by asking (paper §7).
- Placement predicates (`before(kind)`, `after(kind)`) — more predicates in §4, later.
- Teaching the planner `bn` for "at the end of the workday" (#215).
- A Slack surface for `promote_kind` (increment B of #266).
- Retiring `skeleton.gym_placement` into `candidate.required_blocks`.
- Removing `frame_slot`.
- A `closure` kind for the end-of-day closure block, promoted separately once `planning` has run
  for a while.
- Counting two blocks of one required kind on one day — the watcher reports the count and never
  resolves it by picking one, with §4.

## 8. Sequence and tickets

| step | what | ticket |
|---|---|---|
| 0 | Spike: private properties survive a Google UI edit | #210 |
| 1 | Memory: registry, `promote_kind`, sixth judgement, `day_types`, schema v4, reprojection, evals; promote `planning` by hand | #212 |
| 2 | tmbx: render `slug` on read | #211 |
| 3 | Kernel: `REQUIRED_BLOCKS` fact, `candidate.required_blocks`, brief text, submit refusal | #214 |
| 4 | Haunt: `RequiredBlockRule`, re-keyed register, cache write after commit | #213 |

#209 is closed by this document. #128's "one registry, memory owns it" is what §1 builds.
