# Stage-card grammar: turn card, context panel, fold

**Date:** 2026-09-04. **Decision ticket:** #266 (increment B's surface). **Builds on:**
`2026-09-03-stage-ux-port-design.md` (increment A, landed in #273) and the Stage 1 spec
`2026-09-04-stage1-elicitation-design.md`, whose *Content contract* section is the
acceptance test for this document. **Phase 1 plan it sits on:**
`docs/superpowers/plans/2026-09-04-stage1-elicitation-groundwork.md` (Task 9 touches
`StageCard`; this spec builds on those changes, not beside them).

## Problem

Increment A gave every stage one typed card, one renderer and one control table, and
Stage 1 was a date card. The Stage 1 spec turns Stage 1 into an elicitation loop: eight
to twelve probe turns on a working day, over a context of **41 active rules across 24
anchor groups plus 14 unanchored** (the live store on a working Tuesday; the brief's
"~22 rules across ~10 anchors" was the old count). Its content contract asks the card
to render five things: context grouped by anchor, decided items with a deny control, at
most one probe, a gate line, and controls with Next only on `GateMet`.

The existing `StageCard` renders three of the five today. What it cannot do is hold the
context: Slack allows 40 blocks per message (this project's cap, `messages.py`), one
accessory per section, five options per overflow menu. One section per anchor group is
25 blocks before a single decided item, and a button per rule is undrawable. A card
that re-posts 41 rules on every probe turn is ten copies of the same list in one thread,
which is the `msg_too_long` shape increment A rejected as shape C.

So the question this spec answers is not *which* of #266's three shapes (A was chosen
and built) but **how shape A carries a looping stage over a context that does not fit
on a card**.

## Decisions

Each was a fork in the brainstorm with Hugo on 2026-09-04. The choice and the reason.

| fork | choice | why |
| --- | --- | --- |
| base shape | **keep A, extend `StageCard`** | B needs the same shared pieces (A inside B); C exceeds 40 blocks by stage 3 and leaves no thread history |
| where the rows come from | **the snapshot** (`applicable_constraints`, written on every resolve; Phase 1 Task 3/7) | today the rows live only in the kernel's `PlanningContext` and the mapper never sees them; one field read by brief and card gives "same rows, same order" by construction |
| what a Stage 1 turn posts | **a compact turn card; context in a separate panel posted once per stage** | ten probes cost ten small cards, not ten rule lists; the decomposition discipline holds |
| the panel | **folded**: counts by anchor group, a Show rules control, never grows (Hugo: "it should fold anyway") | the legacy constraints panel was folded; an unfolded panel is the card that did not fit |
| how the fold opens | **a modal** | the only Block Kit surface that shows all 41 rows with a steer menu per row in one view (modal cap 100 blocks, fixture day 56); no thread noise; steer effects still appear on the next turn card |
| a rule with several anchors | **once, under its largest group, with an "also" tag** | 17 of 41 fixture rules are multi-anchored; duplicating makes one steer change three places; "largest group" is a count over minted links |
| ordering | **what changed and what is uncertain first, then the store's order** (Hugo's ask) | the row that deserves attention is the one touched this session, the one under a concern with an open cell, or the one about to fade |
| mockups | **real Block Kit JSON, validated with `blockkit`, viewed in Block Kit Builder** (Hugo's ask) | a mock drawn outside the kit invents what the kit cannot render |

Nothing here reopens what the Stage 1 brainstorm settled: the stage is user-ended,
Next exists only on `GateMet`, forcing is a user-filed assumption, button ≡ typed reply
through `SurfaceIntentInterpreter`, headings are anchor names, steering is
session-scoped and promotion asks first.

## Design

### Three surfaces

Each is a typed model with its own builder and renderer; all three are host-side under
`slack_bot/`. None calls a model. None imports `slack_sdk` (AST-guarded, as
`stage_cards.py` is today).

```text
kernel ── (outcome, snapshot) ──▶ map_outcome ──▶ StageCard   ── render_stage_card ──▶ one message per turn
   snapshot, first_shown_with ──▶ context_panel ─▶ ContextPanel ── render_context_panel ▶ one message per stage, edited in place
   snapshot, first_shown_with ──▶ context_fold ──▶ ContextFold ─── render_context_fold ─▶ a modal, opened on demand

press / modal pick ──▶ ArtifactActionMeta ──▶ control table ──▶ TimeboxIntent ──▶ kernel
typed reply ──────────▶ SurfaceIntentInterpreter (row uids as option ids) ──▶ same intents
```

**Turn card**, `StageCard`, unchanged in role: one per kernel turn, receipted on the
next. Phase 1 Task 9 adds `gate: str | None`, `NextControl`, the `GateMet` mapping, the
stage read from `TimeboxRequirements.stage_of(requirement_id)`, and
`DecidedItem.filed_by: Literal["planner","user"] | None` as a typed field. This spec
adds one control to the union and nothing to `context`:

```text
DenyControl     kind: "deny_assumption", assumption_id: str     # drawn on every decided assumption
PromoteControl  kind: "steer_always",   fact_id: str            # drawn on a user-stated fact; asks first
```

`StageCard.context` keeps its current meaning (the skeleton's reasoning line). The
Stage 1 contract's *Context* item is satisfied by the panel and the fold, not by the
card; the Stage 1 spec notes this. `map_outcome(outcome, snapshot) -> StageCard` remains
the name that spec targets.

**Context panel**, new `ContextPanel` in `slack_bot/stage_context.py`, built by
`context_panel(snapshot, first_shown_with) -> ContextPanel`. The second argument is the
row-uid set the registry recorded at stage entry (`None` on the first draw, when the
builder seeds it from the snapshot); ordering key 1 reads it, and one shared
`rank_rows(snapshot, first_shown_with)` serves both builders so the panel and the fold
can never order differently:

```text
AnchorGroup     name: str | None            # None = the unanchored group
                uids: list[str]             # rows in rank order; a rule appears in exactly one group
                must_count: int
ContextPanel    session_key: str
                expected_revision: int
                day_label: str              # from the typed Gate / planning day, "a working Tuesday"
                rule_count: int
                must_count: int
                off_today_count: int        # snapshot.suspended_constraint_count (memory-side, day-type mismatch)
                off_today_reason: str       # the day type, system-minted
                groups: list[AnchorGroup]   # rank order
                suspended: list[SuspendedRow]  # session suspensions: uid, name, reason
                shown_with: frozenset[str]  # row uids + suspension fact ids the panel was drawn from
                first_shown_with: frozenset[str]  # the row uids at stage entry; ordering key 1 reads it
```

`shown_with` is what decides whether the panel needs an edit: the next turn compares the
snapshot's row uids and `suspend:{uid}` fact ids against it. Identifiers this system
minted, set equality, nothing read.

**Context fold**, `ContextFold`, built by `context_fold(snapshot, first_shown_with) ->
ContextFold`, rendered as a modal view:

```text
FoldRow         uid: str, name: str, necessity: "must" | "should"
                applicability: "every day" | "day-specific" | "suspended today"
                also: list[str]                       # the rule's other anchor names
                suspended_reason: str | None          # "you said: not today"
                controls: list[SteerVerb]             # what the overflow offers for this row
SteerVerb       Literal["steer_not_today", "steer_wrong", "restore"]
FoldGroup       name: str | None, rows: list[FoldRow]
ContextFold     session_key, expected_revision, day_label
                groups: list[FoldGroup]               # rank order, same as the panel
                truncated: tuple[int, int] | None     # (rules, groups) folded into the last line
```

A durable row offers *Not today* and *This is wrong*; until map B lands, *This is
wrong* files the same suspension plus a note, exactly as the Stage 1 spec says. A row
suspended this session renders struck through, with its reason, and offers *Restore*.
*Always* is never offered on a durable row: it is already durable. It lives on the turn
card's decided list, on facts the user stated this session.

### Rendering

**Panel** (two blocks, measured 757 JSON chars on the fixture day):

```text
section   *1/5 · Constraints — what I know about a working Tuesday*
          41 rules apply (16 must, 25 should) · 1 rule off today (not a Wednesday)
          *deep work* 6 · *dinner* 5 · *gym* 3 · … · *no anchor* 14
          accessory: [Show rules]
context   _Off for this session: Client attendance days (you said: not today)._
```

The panel never grows: group names and counts are the only variable text, and 24 groups
measured ~400 characters against a 3000-character section. It is posted when Stage 1
opens, edited in place when `shown_with` changes, receipted as *superseded* and
re-posted only on a day change. On Next it stays as it is: it is the record of what the
planner read.

**Fold** (modal, title *Rules for Tuesday 8 September*): per group one heading section,
then one section per rule:

```text
section   *gym*
section   Oats Timing  _must · every day · also breakfast_           accessory: overflow ▾
section   Gym session buffers  _must · every day_                     accessory: overflow ▾
…
section   *commute*
section   ~Client attendance days~  _must · you said: not today_      accessory: overflow ▾ (Restore)
…
section   *what today is not*
          1 weekday rule off because today is a Tuesday
```

Fixture day: 56 blocks against the modal cap of 100. When a day exceeds the cap the
last groups collapse into one line, *"+N rules in M more groups"*; truncation is by
count, never by slicing text, and every truncated rule stays steerable by typing
because the interpreter is offered every uid. The description is not shown: the name is
the rule's own label, minted at write time, and a description in every row is what
pushes a heavy day past the cap.

**Turn card** (at most 15 blocks):

```text
section   *1/5 · Constraints*
section   *Decided*
section   wake 07:00 · sleep 23:00  _(from memory: Sleep schedule)_     accessory: overflow ▾ (Deny)
section   gym at 18:00  _(you, this session)_                          accessory: overflow ▾ (Deny · Always, asks first)
section   assumed: no client visit today  _(you filed this)_           accessory: overflow ▾ (Deny)
divider
section   *Asking*  <probe>
          _<why_needed>_
context   _Or just tell me anything else about tomorrow; I will file it where it belongs._
actions   [option buttons, at most 4]                                   only when the answer set is closed
context   *Gate* · still need: what is fixed · movement and transitions
actions   [Next]  [Back]  [Cancel]                                      Next only on GateMet
```

Decided is capped at `STAGE_LIST_CAP` (8) items with a *"+N more"* line, as today. Each
decided item is its own section because the overflow is its accessory; the cap is what
keeps that under budget. The free-association hint is a design input from the
brainstorm: the surface must make it easy to add a thought, not only to answer the one
probe.

A receipt is the same card through the same renderer with controls dropped and
`done` set, exactly as increment A: *answered* after a probe, *✅ confirmed* on Next,
*↩️ reopened* on Back.

### Ordering

Rows sort by four keys, in this order, and groups take the rank of their top row. The
panel's summary line and the fold use the same order. Every key is arithmetic over
fields the system minted:

1. **Touched this session**: a `SUSPENDED_CONSTRAINT` fact names the rule, or the rule
   was not in the row set the panel was first drawn with (`first_shown_with`, kept in
   the registry record; a rule promoted or restated this session appears that way).
   After a restart the registry re-seeds from the current set and this half of the key
   degrades to suspensions only, which is cosmetic. Not derived from `fade`, and not a
   memory read at render time.
2. **Under an open concern**: the rule's anchor is placed (the matrix fact's
   `placement`) under a row that has an `uncovered` cell.
3. **Nearest to fading**: `ConstraintView.fade: float | None` (Phase 1 Task 1b), which
   the memory server computes in the read path for the requested day as
   `elapsed_days / half_life` clamped to [0, 1], `None` for a decay class with no
   half-life (`PERMANENT`). The half-life never leaves the server. Sorted descending,
   `None` last; 1.0 means "fades tomorrow".
4. **The store's own order**: must before should, then most recently restated
   (`read_api._reading_order`). The list arrives in this order and a stable sort keeps
   it as the tiebreak.

A rule with several anchors is listed once, under the anchor with the most rules on
that day (ties by anchor name), and its other anchors appear as an *also* tag. This
is a count over `constraint_anchors` links, not a judgement about the rule.

### Controls and the table

`ArtifactActionMeta.decision` gains three values, two of them in Phase 1 Task 8
(`deny_assumption`, `restore`, validators and bindings included) and one here
(`steer_not_today`). Each carries exactly the binding its press needs, under a
validator like the one `choose_option` has:

| decision | requires | intent |
| --- | --- | --- |
| `deny_assumption` (Phase 1 Task 8) | `assumption_id` | `DenyAssumption(assumption_id)` |
| `steer_not_today` | `constraint_uid`, optional `note` | `ProvidePlanningFacts([SUSPENDED_CONSTRAINT at suspend:{uid}])` |
| `restore` (Phase 1 Task 8) | `constraint_uid` | `RestoreConstraint(constraint_uid)` (Phase 1 Task 3/6): the kernel deletes the fact at `suspend:{uid}`, invalidates captured inputs and sets `stage1` back to `open`, since a restored rule can uncover a cell. This spec draws the control; the decision and its binding are Phase 1's |
| `steer_always` | `fact_id` | not a kernel intent: the host opens the *promote?* confirmation (a `PendingBlocker` with two options); only the second option calls `memory_observe` |

`show_rules` is not in the table. It is a host action: read the snapshot and the
registry's `first_shown_with`, build `context_fold`, `views_open` with the press's
`trigger_id`. Nothing about the session
changes when the fold opens.

A modal overflow pick carries the same `artifact_action_value` a card button would, so
it decodes through the same table. After the kernel applies it, the host
`views_update`s the modal with a fresh `context_fold` (the row now struck through) and
the thread gets its turn card as for any other intent.

The typed path needs nothing new: the Stage 1 surface already offers
`steer_not_today`, `deny`, `steer_always`, `restore` as decisions and the row uids as
option ids. A button and a typed reply land on the same fact id.

### Data flow, one Stage 1 turn

1. Press, modal pick, or typed reply → one `TimeboxIntent`. Unchanged.
2. Kernel applies → `(outcome, snapshot)`.
3. Registry: the previous turn card becomes a receipt (increment A's `transition`).
4. Panel: if `snapshot` row uids ∪ suspension fact ids ≠ the panel's `shown_with`,
   `chat_update` the panel in place. A day change instead receipts it as *superseded*
   and posts a new one. Best-effort, never fails the turn.
5. `map_outcome` → turn card → posted → registry records its ts.
6. Stage entry (first Stage 1 turn after the day is locked): step 4 posts the panel
   before step 5 posts the card, so the panel sits above the first probe. `GoBack` into
   Stage 1 from Stage 2 edits the panel; it does not re-post.
7. Next (`Advance` on `GateMet`): the turn card receipts *✅ confirmed*; the panel stays.

The registry gains one record per session for the panel, `{channel, ts, panel}`, beside
the turn card's. Host state only; the snapshot never learns a Slack ts.

### Error handling

| case | behaviour |
| --- | --- |
| stale press from the modal (revision mismatch) | refused inside the modal (`views_update` with the existing stale-press copy); nothing posted to the thread |
| `views_open` fails (expired `trigger_id`, Slack refusal) | ephemeral reply: press again; logged |
| fold over 100 blocks | count truncation as above; typing still reaches every rule |
| panel `chat_update` fails | logged; retried on the next turn; the turn card is posted regardless |
| `restore` with no suspension fact, `deny_assumption` on a retired assumption, a uid the snapshot no longer holds | kernel `TurnFailed` (`stale_restore`, or the stale-press code) rendered through the existing failure card |
| `applicable_constraints` empty because memory was unavailable | never reaches the panel: the host's resolve raises `AdaptiveDependencyUnavailable` (Stage 1 spec); the panel is never drawn from an empty set that might be a dead server |
| a rule with anchors the placement did not cover | it is a row under its anchor as usual; only the ordering key 2 treats it as not-under-an-open-concern |

### Testing

Unit tests opt in to the harness backend as `test_timebox_session_surface.py` does.

- **Builders** (`context_panel`, `context_fold`) on fixture snapshots: every rule in
  exactly one group, the largest group wins, the *also* tag lists the rest; each
  ordering key tested alone by a fixture that differs in only that key; session
  suspensions struck through with *Restore*; memory-side suspensions as one line;
  count truncation at the cap with the *"+N rules in M groups"* line.
- **Control round-trip**: for every `StageCard` and `ContextFold` a builder can produce,
  every drawn control decodes through the table to an intent the kernel accepts at that
  revision. Extends increment A's walk.
- **Renderer**: structure and counts only. Panel = 2 blocks; fold ≤ 100; turn card ≤ 15;
  action ids present. **Every rendered surface passes the `blockkit` validator**, added
  as a dev dependency: a section over 3000 characters, an overflow over 5 options, a
  button label over 75 characters fails in CI instead of as a Slack 400. (`blockkit`
  does not check the 50-block message cap; `messages.py`'s 40 stays the guard.)
- **AST guard**: `stage_context.py` imports nothing from `slack_sdk` nor from
  `handlers`, `timeboxing_cards`, `timeboxing_commit`.
- **Transition**: panel edited when `shown_with` changes and not otherwise; receipted
  and re-posted on a day change; edit failure does not block the card.
- **E2e** (extends `tests/e2e/test_slack_timebox_command.py` as increment A planned):
  one panel, ten probe turns → ten cards and one panel; a modal steer edits the panel
  in place and the next card's decided list shows the suspension.
- **No eval tests.** Nothing here asks a model a new question; grouping and ordering
  are arithmetic. Each guard is broken on purpose once before it is trusted.

## Sketches

Built from the live store for Tuesday 2026-09-08 (working day), validated with
`blockkit` 2.1.3, and opened in Slack's Block Kit Builder during the brainstorm. Block
counts are the measured ones; the JSON below is the panel exactly as validated.

| surface | blocks | cap |
| --- | --- | --- |
| context panel | 2 | 40 |
| fold as modal, 41 rules, 24 groups + unanchored | 56 | 100 |
| turn card, gate open, 3 decided items | 10 | 40 |
| turn card receipt | 5 | 40 |
| the rejected full card (context on every turn) | 15 per turn | 40 |
| the rejected per-group thread messages | 7–15 per group | 40 |

```json
{"blocks":[
 {"type":"section","text":{"type":"mrkdwn","text":"*1/5 · Constraints — what I know about a working Tuesday*\n41 rules apply (16 must, 25 should) · 1 rule off today (not a Wednesday)\n*deep work* 6 · *dinner* 5 · *gym* 3 · *shallow work* 3 · *breakfast* 2 · *sleep* 2 · *work* 2 · *commute* 2 · *workday* 2 · *prep food* 2 · *evening shutdown ritual* 2 · … · *no anchor* 14"},
  "accessory":{"type":"button","action_id":"ff_timebox_show_rules","text":{"type":"plain_text","text":"Show rules"},"value":"<artifact_action_value>"}},
 {"type":"context","elements":[{"type":"mrkdwn","text":"_Off for this session: Client attendance days (you said: not today)._"}]}
]}
```

## Coordination

- The Stage 1 spec's *Content contract* is the acceptance test. Items 2 to 5 render on
  the turn card; item 1 renders on the panel and the fold. The sibling session
  (`admonish-1-a2`) has agreed to note that in the contract.
- Phase 1 (`2026-09-04-stage1-elicitation-groundwork.md`) lands first. Its Task 9 owns
  the `StageCard` changes listed above; this spec's implementation plan adds
  `DenyControl`, `PromoteControl`, `stage_context.py`, the `steer_not_today` table
  decision (Task 8 owns `deny_assumption` and `restore`), the modal handler and the
  panel record, and touches nothing Task 8 or Task 9 changed.
- Asked of Phase 1 and agreed on 2026-09-04: `RestoreConstraint(constraint_uid)` in
  Task 3/6 with the `restore` decision bound in Task 8;
  `PlanningSessionSnapshot.suspended_constraint_count: int = 0`, written by the host's
  resolve beside `applicable_constraints` on every turn from
  `get_suspended_constraints` through a new KG-client `count_suspended`, so the panel
  renders it before any classify has run (the matrix's `not_today` row stats are
  downstream of this field); `ConstraintView.fade` in Task 1b, carried on the KG row
  as `"fade"`.

## Out of scope

- The correction path (*This is wrong* as a memory-server operation): map B.
- Anchor hierarchy (#140) and anchor de-duplication (filed against the memory server).
- Stage 2's card depth (priorities, one thing, AVBD): its own spec; the turn card and
  the decided overflow carry over unchanged.
- Relabelling the thread root's *"No active constraints yet"* line: #265's root path.

## Files

New: `src/fateforger/slack_bot/stage_context.py` (models, builders, ordering),
`tests/unit/test_stage_context.py`, `tests/unit/test_render_context_surfaces.py`.
Changed: `stage_cards.py` (`DenyControl`, `PromoteControl`, decided overflow),
`timeboxing_cards.py` (`render_context_panel`, `render_context_fold`, decided overflow,
free-association hint), `timeboxing_intents.py` (the `steer_not_today` decision and its validator),
`stage_card_registry.py` (panel record), `handlers.py` (`show_rules` → `views_open`;
modal pick → table → `views_update`), `pyproject.toml` (`blockkit` in the dev group).
