# Required Blocks — Design Options

**Date:** 2026-08-30
**Status:** Options paper. No decision taken; the decision Hugo has to make is named in §9.
**Issue:** https://github.com/hugocool/FateForger/issues/206 (adaptive timeboxing session kernel), PR #208
**Related:** #140 (promotion/taxonomy gate), #154 (re-projection), #155 (schema ladder)

## The ask

> The last thing I want to add is required blocks, so we can have memory entries that
> define which blocks should always be present, and then we generate them in a way that
> the other agents can easily check whether they exist (and whether they should start
> admonishing if they don't). The prime example of this is the plan session, but you can
> also think about (conditional) sleep blocks, things like routines (morning routines,
> etc). [...] for that tracked event we need to register the id with a watcher so an
> admonisher agent can track it.

Five questions came with it, and §4–§8 answer each one directly: who mints the tracked
id, how it generalises past one block, what happens when half the write succeeds, how the
user gets involved when the planner only knows "one of these must exist", and whether the
constraint can carry an anchor like "at the end of the workday".

The short version of what follows: most of the machinery exists, one piece of it is built
on the thing this project bans, and the choice worth arguing about is not who mints an id
— tmbx already mints every one — but whether presence is **asserted** into a register or
**derived** from the calendar.

---

## 1. What is already built

### 1.1 The watcher exists, and it half-works

`src/fateforger/haunt/` already contains a planning-session watcher, wired into the
runtime and running daily.

- **`PlanningGuardian`** (`src/fateforger/haunt/planning_guardian.py`) runs
  `reconcile_all` on a cron at 06:00 UTC (`schedule_daily`, line 30), iterating every row
  in the anchor store. It also has `schedule_reconcile_after_deletion`, which reacts to a
  deleted planning event by shifting `now` backwards so the first nudge lands in five
  minutes instead of ten.
- **`planning_anchors`** (`src/fateforger/haunt/planning_store.py`) holds **one row per
  user** — `UNIQUE(user_id)` — carrying `calendar_id` and a single `event_id`. It is not
  per day. It is a pointer to "the user's planning event", singular and eternal.
- **`planning_session_refs`** (`src/fateforger/haunt/planning_session_store.py`) *is* per
  day: `UNIQUE(user_id, planned_date)` and `UNIQUE(calendar_id, event_id)`, with a
  `status` of planned/in_progress/completed/cancelled, plus `title`, `event_url`,
  `source`, `channel_id`, `thread_ts`.
- **`PlanningSessionRule.evaluate`** (`src/fateforger/haunt/reconcile.py:157`) resolves
  presence in three descending tiers, logging `anchor_found` / `anchor_in_window` /
  `stored_hit` / `fallback_hit` on every pass:
  1. `get-event` on the anchor's `event_id`, checked against a 24h window;
  2. the stored `planning_session_refs` rows for the day, each re-fetched from the
     calendar, with a five-minute "consistency bridge" that trusts a local row the
     calendar has not caught up with yet;
  3. failing both, a **scan of the day's event titles**.

When all three miss, it schedules escalating nudges (10m, then exponential backoff to a
cap of 8h, max 5 attempts) plus an `expire` job at the horizon.

### 1.2 Tier three is the thing this project bans

`_planning_event_score` (`reconcile.py:518`) scores every event on the day by its title:

```python
exact_titles = {"planning", "planning session", ..., "timebox session"}
...
if any(keyword in summary for keyword in self._config.summary_keywords):
    score = max(score, 30)

# Guardrails against social/planning-adjacent false positives.
if " with " in summary and "session" not in summary:
    score -= 40
if "poker" in summary:
    score -= 40
if "wife" in summary:
    score -= 25
```

`reconcile.py` also imports `re` (line 4) and uses it to normalise event summaries before
matching (`_normalize_summary_text`, line 732). This is exactly the shape CLAUDE.md
forbids, and the code already knows it — `PlanningRuleConfig` carries the note:

```python
# TODO(refactor,typed-contracts): Remove summary keyword list and use a typed
# event marker/label schema for planning-session detection.
summary_keywords: tuple[str, ...] = ("plan", "planning", "review", "timebox")
```

The scoring exists because tiers one and two are unreliable, and they are unreliable for
one reason: **nothing on the tmbx write path ever registers anything.** Grepping
`planning_session_store` across `src/` finds three writers — the Slack planning card's
"Add to calendar" handler (`slack_bot/planning.py:1157`), the fallback scanner writing
back what it guessed (`reconcile.py:497`), and the anchor refresh
(`_maybe_refresh_anchor_from_refs`). A planning block that reaches the calendar through
`plan_commit` is invisible to all three.

So: the watcher is built, the register is built, and the one path that actually writes
blocks to the calendar does not feed either.

### 1.3 tmbx already mints every event id

This settles Hugo's "maybe the parser mints the id itself" before the options begin.
`src/tmbx/service.py:105`:

```python
def _mint_event_id() -> str:
    """Mint a random, opaque event id valid as a Google custom event id."""
```

`tmb0` plus twenty characters drawn from the base32hex alphabet, validated by a plain
string predicate. `GoogleCalendarAdapter.create` passes it through explicitly
(`src/tmbx/calendar/gcal.py:214`):

> `eventId` is passed explicitly (the tool supports a custom id) so the provider event id
> matches what the caller — `PlanService`, via its `uid -> event_id` map — already
> believes it to be.

**Google never mints a tmbx event id.** The capability Hugo was asking whether we have,
we have, and it is load-bearing in the existing design. There is precedent on the Slack
side too: `planning_event_id_for_user` (`src/fateforger/slack_bot/planning_ids.py`) mints
`"ffplanning" + b32hex(sha1(user_id))` — deterministic per *user*, which is why the anchor
table has one row per user and cannot represent a planning session that recurs daily.

What tmbx does **not** do is tell anyone what it minted. `plan_commit` returns
`{"committed": true, "tx_id": ..., "conflicts": [...]}` and nothing else
(`src/tmbx/server.py:525`). No uid, no event id, no map. A host that just committed a day
containing a planning block has no way to learn which calendar event it became short of
re-reading the day.

### 1.4 Identity in tmbx is already three-layered, and one layer is dormant

`Block` (`src/tmbx/core/models.py:190`) carries:

- `uid` — server-minted, opaque, durable; round-tripped through
  `extendedProperties.private["tmbx.uid"]`. This is the durable identity: `_write` rebuilds
  its `uid -> event_id` map from the live calendar on every commit, so the event id is
  *derived* from uid, never the other way round.
- `h` — the addressing handle, valid for the turn it was rendered in.
- `slug` — "*Pattern identity. Names the recurring kind of block, stable across days. What
  memory anchors attach to.*"

`slug` is written to `extendedProperties.private["tmbx.slug"]` on every commit and read
back on every `list_day`. It is in the `add` and `update` op schemas. And it is
**dormant**: no prompt mentions it, `render_plan`'s columns are
`("H", "own", "type", "summary", "ST", "ET", "mode", "dur")` — no slug — and nothing in
`src/fateforger/` ever sets one. The planner cannot see a slug on a block it did not just
write, so it cannot tell whether the slug is already taken.

The field was designed for exactly this feature and has never been used.

### 1.5 The memory side has no way to say "this must exist"

`Constraint` (`src/memory/constraint.py:123`) carries `necessity`, `scope`, `status`,
`source`, `frame_slot`, `tier`, `applicability`, `decay_class`, `last_observed_at`, and
`source_observation_uids`. `ConstraintView` — what the planner actually receives — drops
`applicability`, `tier`, `decay_class` and anchors, keeping eight fields.

`necessity` is the wrong axis. Its prompt (`src/memory/prompts.py:94`) asks whether the
person *would reject the plan outright*, which grades firmness, not existence. A rule can
be MUST and be about a duration; a rule can be SHOULD and be about a block that has to be
on the day. Nothing in the schema distinguishes them.

The rules Hugo means are already in the store, as prose, unactionable. Reading the live
corpus for a working day returns among forty-one rows:

- *"End-of-day closure block: Reserve 15-20 minutes at the end of the workday to update
  artifact links and board status."* — `necessity: should`
- *"I timebox my day by allocating fixed blocks for tasks and activities."* — `should`

The first is Hugo's example almost verbatim, including its anchor ("at the end of the
workday"), and there is no field on it that any checker could read.

Two fields are shaped like the answer and are dead:

- **`frame_slot`** exists on `Constraint` and `ConstraintView` and is **never written by
  the memory server** — projection does not set it, so every constraint the KG server
  mints has `frame_slot=None`. Only the legacy `src/fateforger/agents/timeboxing/`
  extractors produce one, where it is null on 94% of rows (`agent.py:6952`).
- **`applicability.day_types`** has a reader (`applies_on`, `constraint.py:101`) and no
  writer — no prompt asks for it. Existing values in the live store predate the code.

`stage` is accepted by `get_active_constraints` and unused: "*it is part of the agreed
call shape and will select stage-relevant constraints once stage vocabulary exists*"
(`src/memory/read_api.py:45`).

### 1.6 The read path is closed to models, and the closure is enforced

`tests/memory/test_read_api.py:88` walks the AST of `read_api.py` and asserts its imports
are a subset of `{__future__, datetime, memory.constraint, memory.constraint_store}`, and
that the module contains no `async def` and no `await`. A duplicate of the same guard sits
in `test_decay_read.py:73`. Whatever "required block" becomes, deciding *whether a rule is
a required-block rule* cannot happen at read time. It has to be decided once, at
projection, and stored structurally.

### 1.7 The planner side already refuses to ask about things it owns

- `readiness.py` holds a nine-entry `_REQUIREMENTS` tuple, each with an `owner`
  (PLANNER/SYSTEM/USER), a `resolution` (assume/ask/fetch/validate), a `why_needed` and a
  user-facing `question`.
- `adaptive_timeboxing.py:740` refuses a planner turn that returns a blocker on a
  planner-owned requirement: `TurnFailed(code="illegal_user_blocker", message="The
  planner delegated a planner-owned decision.")`
- `infra/dsh/profile/memory-policy.md:126` states it to the model: "**A gap you own may
  not become a user question.**" And at :131: "*A blocker on a placement you own is
  refused, because it is the same stalling turn wearing the schema.*"
- `BlockerOption` exists (`session_contracts.py:222`) with host-minted `option_id`s,
  max four per blocker, and the standing note that empty options are the ordinary case:
  "*a planner that offered four guesses at an open question would hide the fifth answer
  the user had.*"

The catalog also contains the cautionary precedent: `skeleton.gym_placement`, a
requirement hardcoded for one activity, with `_is_satisfied` branching on its
`requirement_id` by hand (`readiness.py:273`). Adding required blocks the same way means
one entry, one branch and one deploy per block Hugo wants tracked. That is the
special-casing the design has to avoid, and it is not hypothetical — it already happened
once.

### 1.8 One naming hazard

`PlanningSessionSnapshot` (`agents/timeboxing/session_contracts.py`) is the *conversation*
kernel's state. `PlanningSessionRef` (`haunt/planning_session_store.py`) is the *calendar
event*. They are unrelated and both are called a planning session. Whatever is built
should not add a third.

---

## 2. What a required block actually has to be

Three separable pieces, and every option below is a different answer to the third:

1. **A statement** — a memory entry that says: a block of kind K must be on the plan for
   any day matching applicability A, with strength N. Structural, so the model-free read
   path can serve it.
2. **A mark** — something on the committed block that says *this block is of kind K*, in
   an identifier this system minted rather than in its title.
3. **A join** — a checker that reads (1) and (2) and answers "is it there?" without asking
   a model what an event's title means.

Piece (2) is the one CLAUDE.md constrains hardest. `Block.n` is unconstrained user prose;
deciding that an event named "Weekly review + planning" is the planning session is a
judgement about meaning and belongs to a model. But `tmbx.slug` is an identifier this
system minted, and comparing two of those for equality is explicitly *not* covered by the
ban: "*String operations on identifiers the system itself minted [...] carry no meaning
about the user.*" That is the whole reason a mark beats a title.

---

## 3. The options

### Option A — A patcher tool that creates the tracked event and registers it

The planner calls a dedicated tmbx tool — `plan_ensure_tracked(slug, ...)`, or a `track`
argument on `plan_commit` — that writes the event and returns its id. The Slack host then
writes `planning_session_refs`. This is Hugo's first sketch.

**How it works.** The tool takes the slug from the constraint, mints the id (as tmbx
already does), writes, and returns `{slug: event_id}`. The host upserts the register.

**Generalisation.** Fine in principle: the tool is parameterised by slug, so N tracked
blocks need no new code. In practice it invites a second write path alongside
`plan_commit`, and `plan_commit` is where drift checking, foreign-block guarding, plan
resolution and journaling all live (`service.py:745`). A second door into the calendar is
a second place all five of those can drift out of agreement, and the first four exist
because they were each once absent.

**How it fails.** Two writers (§6). The tool succeeds, the register write fails, and the
watcher nags about a session that is on the calendar — the worst failure direction,
because the user learns to ignore the nudge. Under retry it needs its own idempotency
story: `plan_commit`'s key is `hmac`-checked against the SHA-256 of `snapshot+patch`
(`server.py:456`), and a separate tool has a different payload and therefore a different
digest.

**Cost.** New tool, new prompt surface for the model to get wrong, new refusal codes, and
the register stays authoritative — which means every Google-side edit is a chance for it
to go stale.

### Option B — A flag on the block; the parser mints the id and registers it

Hugo's second sketch: `Block.tracked: bool` (or better, `Block.required_of: <slug>`), and
`PlanService._write` registers any block carrying it.

**On minting.** This buys nothing on the minting axis, because `_write` already mints and
already passes `eventId`. The flag's real content is "*tell someone about this one*" — it
is a notification request, not an identity mechanism. Worth naming, because the sketch's
appeal came from the belief that Google was minting the id.

**Generalisation.** A boolean does not generalise: with two tracked blocks on one day, the
register cannot tell which row is which, and Hugo asked about exactly this. A slug-valued
field generalises fine — but then it is `Block.slug`, which already exists, and the flag
adds nothing but a second name for the same thing.

**How it fails.** The flag is set by the model, per turn. It can be forgotten, and a
forgotten flag is silent: the block is on the calendar and unregistered, so the watcher
nags. Worse, it ties whether a standing rule is enforced to whether the planner remembered
to say so this turn, which is the inversion `anchor_source="constraint"` exists to prevent
elsewhere in this codebase.

**Cost.** A schema change to `Block` and to the op vocabulary, plus a write from inside
`PlanService` to a `fateforger` store — which `src/tmbx/` may not import
(`journal/constraint_refs.py:3`: "*Duck-typed on purpose: this module must not import
fateforger*"). It would need a port and an injected sink, which is real work for a
mechanism whose reliability is worse than Option C's.

### Option C — Presence is derived: the mark is the slug, and the checker reads the calendar

No registration write at all. The constraint names a slug; `plan_commit` writes the slug
into `extendedProperties.private["tmbx.slug"]` as it already does; the reconciler lists
the day and asks whether any event carries the required slug.

**How it works.** Nothing changes in the write path. The reconciler replaces
`_planning_event_score` with set membership over slugs — an identifier comparison, not a
judgement. `planning_session_refs` survives as a **cache**, rebuildable from the calendar
on any reconcile, rather than as the source of truth.

**Generalisation.** One row per required-block rule in memory; zero lines of code per
block. Sleep, morning routine and planning session differ only by slug and applicability.

**How it fails.**

- *A block Hugo books by hand in Google Calendar has no slug.* The watcher does not see
  it and nags. This is the case the title scanner was written for, and the honest
  replacement is to ask rather than guess — the nudge already has a card, and "is this
  one it?" against the day's events is a question with a genuinely closed answer set,
  which is what `BlockerOption` is for. Adopting a user-created event writes the slug onto
  it, so it is a one-time question per event, not per day.
- *It depends on Google preserving `extendedProperties.private` across a UI edit.* Moving
  and renaming an event through the Google UI should preserve them — the API treats them
  as opaque metadata — but this has not been verified against the live account, and the
  design rests on it. §9 lists it as a decision-blocking spike, not an assumption.
- *Two events could carry the same slug on one day.* Nothing forbids it. The check should
  report the count and not pick a winner; `make_snapshot` already refuses a duplicate
  `uid` for the same reason.

**Cost.** Two dormant things have to wake up. The planner has to set `slug`, which means
`plan_read` must render it — otherwise the planner cannot see whether the slug is already
taken and will add a second one. And the reconciler needs the private extended properties
off its own calendar read. That part is nearly free: `gcal._event_from_payload` already
pulls `extendedProperties.private` out of a `list-events` response from the same MCP
server, with the same argument shape the haunt client already sends, and the haunt
client's `_normalize_event` passes the raw payload through untouched. So the properties
should already be arriving; only the reading of them is missing.

### Option D — Do nothing structural; strengthen the constraint text and keep the scanner

Named because it is the status quo and deserves to be argued against rather than skipped.

**How it works.** The rule stays prose. The scanner keeps scoring titles. `memory-policy.md`
gets a paragraph telling the planner to always include a planning block.

**Generalisation.** None. Each new tracked block needs a hand-written vocabulary and its
own set of negative guardrails. The existing guardrails subtract points for "poker" and
"wife"; a sleep block would need its own, and nobody would find out they were wrong
because a wrong pattern does not raise.

**How it fails.** Silently, in both directions, forever. It is also the one option that
leaves a CLAUDE.md violation in the tree with a TODO on it.

**Cost.** Zero to build, and it is the only option with no spike in front of it. If the
answer is "not now", this is what "not now" means, and it should be chosen deliberately
rather than by default.

---

## 4. Who mints the tracked id, and when

**tmbx does, at commit, and it already does.** The design question is not minting but
*telling*, and the strongest answer is that nobody needs telling — presence is re-derived
(Option C). Here is how each option behaves under the three failure modes Hugo named.

**When a turn is retried.** `plan_commit` is idempotent on an `idempotency_key` that must
equal the canonical SHA-256 of `snapshot + patch`; a repeat returns the same `tx_id`
without a second calendar write (`service.py:698`). But a *rebuilt* patch against a fresh
snapshot is a different digest and therefore a real second commit.

- Under C: the block is matched by uid from the live calendar, so a genuinely unchanged
  block is skipped by `_event_unchanged` and keeps its id. If it was removed and re-added
  it gets a new uid and a new event id — and the checker still finds exactly one slug, so
  nothing downstream notices or needs to.
- Under A and B: the register now names an event id that may no longer exist. The existing
  code already carries the scar tissue for this — `_maybe_refresh_anchor_from_refs`
  (`slack_bot/planning.py:579`) exists to repair a stale anchor after the fact.

**When a commit is refused.** `plan_commit` refuses on `stale_snapshot`, `foreign_block`,
`plan_violation`, `invalid_patch` and `malformed_input`, and writes nothing.

- Under C there is nothing to unwind: no registration happened because registration is not
  a step.
- Under A and B the ordering matters and both orders are wrong. Register first and a
  refusal leaves a row pointing at nothing. Register second and a crash between the two
  leaves an event nobody is watching.

**When the user edits the event in Google Calendar afterwards.**

- Under C, a move or a rename carries the slug with it and the check still passes. A
  delete removes it, and the watcher fires — which is the behaviour we want, obtained by
  construction rather than by a deletion hook.
- Under A and B, a move is invisible (the id is unchanged, so the register is still
  right), but a delete-and-recreate produces a new id the register does not know, and the
  watcher goes quiet on a day where the block genuinely is present under a new id — or
  fires on a day where it is present. Which of the two depends on whether the user
  recreated it, and the register cannot tell.

---

## 5. How it generalises to several tracked blocks

The generalisation lives entirely in whether "which block" is a *value* or a *branch*.

Under C it is a value at every layer: a memory rule names a slug; the checker takes the
set of slugs required for the day and the set present on it; the difference is what to
admonish about. Nothing in code names "planning session". Adding a conditional sleep block
is one `memory_observe` call and no deploy.

The place this can still go wrong is the requirement catalog. `_REQUIREMENTS` is a static
module-level tuple and `_is_satisfied` branches on `requirement_id` by hand, so the
tempting move — one `ArtifactRequirement` per required block — reproduces
`skeleton.gym_placement` N times. The alternative is **one** catalog entry,
`skeleton.required_blocks`, `owner=PLANNER`, `resolution="assume"`, whose satisfaction is
computed over the required-slug set rather than hardcoded. That keeps the number of
catalog entries at one regardless of how many blocks Hugo tracks, and it inherits the
`illegal_user_blocker` refusal for free.

One caveat found while checking this. `applicable_constraints` does reach the model at
every stage — `deepseek_timebox_planner.py:127` fills it unconditionally. But the
*kernel's* context port returns an empty `PlanningContext()` for any target other than
`VALIDATED_CANDIDATE` (`slack_bot/handlers.py:1621`), deliberately, so that Stage 3 does
not touch the calendar. So `FactKind.ACTIVE_CONSTRAINTS` is absent from the typed facts at
skeleton time. A readiness rule that needs to know the required-slug set at skeleton time
cannot get it from the facts as they stand; it needs the set threaded in some way that
does not make Stage 3 read the calendar. That is a real constraint on the design of the
catalog entry, not a bug.

---

## 6. Reliability: this is a two-writer problem

Say it plainly: the calendar and the local register are two stores. Any design that
*asserts* registration performs two writes with no transaction between them, and there is
no ordering that makes a partial failure harmless.

| | Event written, register failed | Register written, event failed |
|---|---|---|
| **A / B (assert)** | Watcher nags for a block that exists. User learns to ignore nudges. Repaired only by the title scanner — the thing being retired. | Register points at nothing. `get-event` misses, falls through to tier two, which re-fetches and misses, then tier three. Self-healing only via the scanner. |
| **C (derive)** | Impossible — there is no second write. Register is a cache; the next reconcile rebuilds it from the day's events. | Impossible — same reason. |
| **D (status quo)** | n/a — the register is already only opportunistically written, which is why the scanner carries the load. | n/a |

The asymmetry is the argument. Under A and B, both partial failures are repaired by
falling back to the mechanism the whole exercise exists to delete. Under C, the register
holds nothing that cannot be recomputed, so it is allowed to be wrong for one reconcile
cycle and no longer.

There is one honest cost to C: the reconcile becomes a calendar read where it used to be
able to short-circuit on a local row. `PlanningSessionRule.evaluate` already lists the day
whenever the first two tiers miss, so the additional cost falls only on the path that
currently short-circuits — a per-user `get-event` traded for a per-user `list-events`,
once a day, plus whenever a nudge revalidates.

---

## 7. How the user gets involved

The planner knowing only "a planning session must exist" is, under the existing ownership
rules, **not a question for the user**. It is a planner-owned placement, and
`memory-policy.md:126` already says so: "*A gap you own may not become a user question.*"
Asking "when would you like your planning session?" is precisely the `illegal_user_blocker`
failure (`adaptive_timeboxing.py:740`), and it is the shape of the 2026-08-29 incident
that produced #206 in the first place — the model asked about gym placement after Hugo had
delegated it.

So the correct behaviour is: place it, and label it. The attribution vocabulary is already
specified and is the one thing Hugo asked for by name (`memory-policy.md:146`):

```
(you said: …)      he told you, in this conversation.
(from memory: …)   a stored constraint. Name which one.
(assumed: …)       you inferred it. Say what from.
```

A required block placed from a standing rule is `(from memory: End-of-day closure block)`
for its *existence* and `(assumed: …)` for its *time*, and those are two different claims
about the same block. The existing three-way split can carry that; nothing new is needed.

Two places a user question is legitimate, and both already have machinery:

- **No feasible placement.** That is a typed infeasibility, submitted as a blocker naming
  the requirement it conflicts with, per `memory-policy.md:128`. `BlockerOption` supplies
  the buttons where the answer set is closed — "shorten SW2 by 20m" against "drop the
  market run" is closed; "when do you want to plan?" is not, and should stay a text box.
- **Adopting an event the user created by hand.** Under Option C this is the only place a
  question is needed at all, and it is a good question: the answer set is the day's
  unslugged events, which is closed and small. Answering it writes the slug, so it is
  asked once per event and never again.

And the nudge itself is a third surface that already exists —
`dispatch_planning_reminder`, the time-picker modal, `start_add_to_calendar`. Nothing in
this design changes it; it changes only what makes it fire.

---

## 8. Anchoring: "at the end of the workday"

Hugo asked whether the constraint could carry an anchor, and how that would express itself
in tmbx's timing vocabulary. Three separate things are called an anchor in this system and
only one of them is about time.

**Memory anchors are topics, not times.** `Anchor` is `{uid, name}`; edges are `IS_A` and
`PART_OF`. `resolve_anchor_names` maps a day's activity names to uids so the read path can
do set membership. That machinery says a rule is *about* the workday; it cannot say a
block goes *at the end of* it. And `anchor_edges` is deliberately unpopulated pending #140,
so passing `anchor_uids` narrows to "constraints whose own anchor is a seed, plus every
unanchored constraint" — which is why `kg_constraint_client.py:98` does not pass them at
all.

**`Applicability` is dates and weekdays, not positions.** `start_date`, `end_date`,
`days_of_week`, `day_types`. There is no field for "relative to X".

So today "at the end of the workday" can only live in `description` prose — which is
exactly where it already lives, in the End-of-day closure block rule.

**In tmbx's vocabulary, `bn` is the right expression, and it is unused.** The four modes
are `ap` (duration, starts when the previous block ends), `bn` (duration, ends when the
next block starts), `fs` (pinned start + duration), `fw` (pinned window). "At the end of
the workday" means *flush against whatever ends the workday* — the commute, dinner, the
gym — and that is `bn` and nothing else. `ap` after the last work block says "somewhere
after it" and leaves a gap. `fs` at 17:40 says a thing the rule did not say.

`bn` is fully implemented — a backward resolution pass in `Plan.resolve`, a
`UNANCHORED_BEFORE_NEXT` violation kind, round-tripping through `timing_mode` — and
`PLANNING_POLICY` defines it in one line and never gives it a case. Grepping `src/` finds
no producer of a `BeforeNext` outside the model definitions and `service._reconstruct_timing`.
It is a mode the planner has been told exists and never told when to use.

Two things follow.

*The failure mode is hard, not soft.* `bn` requires a following block; with none, `resolve`
raises `UNANCHORED_BEFORE_NEXT` and the whole commit is refused. On Hugo's stored rules
something always follows the workday (Work Stop Constraint: stop at the gym, 18:00), but a
required block expressed as `bn` at the very end of a day would refuse the plan rather than
degrade. That is arguably correct and it should be a stated choice.

*The fallback is invisible.* If the planner cannot express the relation it pins the block,
and a pin from a standing rule is written `anchor_source="constraint"` — which puts it in
`BOUNDARY_ANCHOR_SOURCES` and makes it **exempt from `overspecified()`** and refused for
relaxation by `validate_patch`. The check goes quiet on exactly the case it should notice.
This is not speculative: the patch-order spike
(`docs/superpowers/research/2026-08-30-patch-order-spike.md`) records the model, refused
its thirteen relative ops, pinning all fourteen blocks to wall-clock times four seconds
later.

So there are two honest routes and they are not exclusive:

1. **Leave the anchor as prose and teach `bn`.** The constraint keeps saying "at the end
   of the workday"; `PLANNING_POLICY` gains a case for `bn` and the eval measures whether
   the model reaches for it instead of a pin. Cheapest, and it is a prompt change with a
   measurable outcome, so it can be validated by resampling rather than by argument.
2. **Give required-block rules a typed placement hint.** A structural field naming a
   relation and a reference slug. This is a schema change to memory *and* a new extraction
   judgement — "which block does this one sit against?" is a question about meaning and
   goes to a model, once, at projection. More expensive, and it is the only route that
   lets the checker verify *placement* rather than only *presence*.

Route 1 is enough to ship required blocks. Route 2 is what "the constraint could contain
some kind of anchor" would actually mean, and it should not be bundled into the same
change.

---

## 9. What is genuinely new

| Piece | State today |
|---|---|
| Daily reconcile job over users | Built (`PlanningGuardian.schedule_daily`) |
| Escalating nudges + expire, with backoff | Built (`PlanningSessionRule.evaluate`) |
| Per-day register keyed by (user, date) and (calendar, event) | Built (`planning_session_refs`) |
| Client-minted, opaque, valid Google event ids | Built (`_mint_event_id`, passed as `eventId`) |
| Durable per-block identity surviving rename/retime | Built (`tmbx.uid` in extended properties) |
| A pattern-identity field on the block, written to the calendar | Built and **dormant** (`Block.slug`) |
| Presence detection by event title | Built and **should be deleted** (`_planning_event_score`) |
| Planner-owned vs user-owned decisions, enforced | Built (`illegal_user_blocker`) |
| Typed assumptions with attribution | Built (`PlannerAssumption`, `memory-policy.md`) |
| `bn` timing mode | Built, never used, never recommended |
| — | — |
| A structural "this block must exist on days like this" in memory | **New** |
| A slug the planner can see and set deliberately | **New** (field exists; render + policy + instruction do not) |
| A presence check over minted marks rather than titles | **New** |
| One requirement-catalog entry covering N required blocks | **New** |
| `plan_commit` reporting what it wrote | **New** (needed only under A/B; useful anyway) |
| A typed placement relation on a constraint | **New**, and deliberately out of scope here |

---

## 10. Decisions to make before any of this is worked

1. **The join key.** `Block.slug`, `Constraint.frame_slot`, or an anchor uid? `slug` is
   the only one with a working write path to the calendar; `frame_slot` is null on every
   row the KG server has ever produced; anchors are topics and the graph they need is
   gated behind #140. The paper's reading is that `slug` is the only live candidate, but
   the constraint side then needs a field naming a slug, and that is a new field either
   way — so the choice is really "does the memory server learn about slugs, or does
   something between memory and tmbx own the mapping?"

2. **Does `extendedProperties.private` survive a Google UI edit** — drag to a new time,
   rename, duration change, and the Google-side "duplicate" action? Option C rests
   entirely on yes, and the tmbx write path has only ever been exercised against events
   it wrote itself. This is a measurement against the live account, not a judgement, and
   it blocks the decision. Reading the properties back through the haunt client is a
   smaller question and rides along with it.

3. **Assert or derive.** The paper argues for derive (Option C) on the strength of §6 —
   both partial failures under A/B are repaired only by the mechanism being retired. That
   argument is only as good as the answer to (2).

4. **Placement, or only presence.** Route 1 in §8 verifies that a required block exists.
   Route 2 would verify it is in the right place. Shipping presence first is defensible;
   pretending presence is placement is not.

5. **What happens to a block Hugo booked by hand.** Ask and adopt (writing the slug),
   or nag and let him move it. Ask-and-adopt is one question per event; nag is one
   question per day forever.

---

## 11. Tickets

| # | What | Blocked by |
|---|---|---|
| #209 | Decide the join key, and assert vs derive | #210, if the answer leans derive |
| #210 | Spike: do private extended properties survive a Google UI edit? | — |
| #211 | tmbx: wake up `Block.slug` — render, document, instruct | #209 |
| #212 | memory: a structural "this block must be on the day" | #209 |
| #213 | haunt: find the session by a minted mark, not by scoring its title | #209, #210, #211 |
| #214 | kernel: one planner-owned requirement covering every required block (#206) | #209, #212 |
| #215 | tmbx: "at the end of the workday" should be `bn`, not a pin | — |

#210 and #215 can start now. Everything else waits on #209, which waits on Hugo.
