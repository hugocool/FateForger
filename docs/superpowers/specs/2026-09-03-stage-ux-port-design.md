# Stage UX port onto the harness — design

**Date:** 2026-09-03
**Decision ticket:** #266. **Defects it resolves:** #259, #260, #261, #262, #263, #264, #265, #267.
**Maps:** #157 (rebuild), #121 (timeboxing session). **Prior kernel spec:**
`2026-08-29-adaptive-timeboxing-session-kernel-design.md`.

## Problem

The second live run of 2026-09-02 (Slack thread `1788336557.980289`) showed that the harness
backend (`FF_TIMEBOX_BACKEND=harness`, now the default) produces a working *kernel* and a
broken *conversation*. The user never sees which stage they are in, cannot go back, cannot see
what the planner read from memory or the calendar, cannot steer an assumption except by
retyping the whole thing, and one card renders blank because the renderer reads a field the
artifact does not carry. Proceed jumps skeleton → candidate → Approve with nothing in between.

The legacy agent had all of this (`stage_gating.py`, `agent.py`
`_format_stage_message` / `_render_stage_action_blocks`, `constraint_review.py`,
`timeboxing_stage_actions.py`) and the harness port kept the kernel and dropped the surface.

This spec ports the surface without re-importing the legacy agent's monolith.

## The five stages (user-corrected 2026-09-02)

The stage line the user sees. Kernel artifact kinds map onto it; the user never sees an
artifact kind.

| Stage | Name | What happens | Kernel artifacts |
|---|---|---|---|
| 1/5 | **Constraints** | Day type first (working / weekend / …). Then load the initial constraints — memory rules for the day, calendar immovables, standing constraints — and *evoke the user to state tomorrow's constraints that would otherwise go unspoken*: fixed meetings, commute, gym, sleep target, anything unique. Mirror back for confirmation. | `planning_day`, `day_frame`, `captured_inputs` (constraint part) |
| 2/5 | **Priorities** | Progressive drill-down grounded in the productivity principles this system is built on: show what the user wants out of the day → the focusing question → one thing, plus up to two conditional items; each AVBD-checked; floating vs windowed. | `captured_inputs` (activity part); later a `priorities` artifact |
| 3/5 | **Sketch** | A loose skeleton. No optimisation, no breaks, no buffers. Generated fast. Markdown `# {part of day / day anchor}` with `-` bullets, no forced template beyond that. Carries brief reasoning and "talks it back". | `skeleton` |
| 4/5 | **Refine** | The validated candidate with the Revise / Back loop against tmbx. | `validated_candidate` |
| 5/5 | **Commit** | Receipt. | `commit_receipt` |

`planning_brief` is internal and never gets a card.

**Task marshalling** (backlog refinement — inbox zero, close-out, tomorrow preview with 1–3
MITs, stale purge, brain dump) is a *separate step before timeboxing*. Stage 2 consumes its
output; it does not perform it. In this spec stage 2 reads what the user types. A "from your
board" section exists on the stage-2 card from the first increment and is empty until Notion
and TickTick are wired (own grilling ticket on map C, filed alongside this spec).

## Research that shaped stage 1 and stage 2

From the library index (`librarian-index`, 639 documents) and the user's Notion:

- **Implementation intentions** (Atomic Habits, ch. 5): "I will *X* at *TIME* in *PLACE*" —
  91 % follow-through against 35 % without. This is why stage 2 asks *windowed or floating* for
  each committed item and why stage 3 places items against day anchors rather than listing
  them.
- **Standardise before you optimise** (Atomic Habits): stage 3 is deliberately unoptimised.
  Buffers and breaks are stage 4's job.
- **Decisive moments / two-minute rule** (Atomic Habits; Allen's 2-minute rule appears only as
  an endnote): stage 2 does not schedule two-minute items; they belong in marshalling.
- **Generative Agents** (Park et al.): a day plan as 5–8 coarse chunks, recursively refined.
  That is stage 3 → stage 4 exactly, and the reason the sketch is a short markdown outline.
- **Notion "🎯 Task Marshalling — Daily EOD Session"**: five steps; **AVBD** test (Action
  verb / Visible done / Bounded / Dated-or-delegated); red flag "Tomorrow > 7 MITs → 3
  committed + 2 conditional". Stage 2's cap of one thing + two conditional comes from here.
- **Notion "⏱️ Timebox Agent — Standing Context & Read Sources"**: session-open read order is
  latest Weekly Review row (Intention, Must Outcome, Timebox Directives, Start/Stop/Continue,
  Risks) → pending Outcomes → in-progress Tasks. Standing constraints: no screens after 23:00,
  bed-ready 22:30, finance blocks fixed, lunch, sports fixed, commute; deep work mornings only.
  Outcomes that retire high-exposure assumptions are non-negotiable — flag before deferring.
  These are the sources the stage-1 *context* section will show once wired; the standing
  constraints already live in memory.
- **April 2026 Notion prompt** for the constraint interview: fixed meetings, commute, gym,
  sleep target, anything unique; mirror back. This is stage 1's evocation text.

Not available as text in the index (dead stubs or blank pages): Deep Work, GTD, Essentialism,
Make Time, The ONE Thing. Their principles are applied from the user's own summaries in Notion,
not from the books.

## Approaches considered

**Shape 1 — one typed card model, one renderer, one control table (chosen).** Every stage is a
`StageCard`. A per-artifact mapper turns `(outcome, snapshot)` into a `StageCard`; one
renderer turns a `StageCard` into blocks; one table turns a control press into the same
`TimeboxIntent` the typed interpreter produces. Stage transitions edit the previous card into a
receipt and post the next card.

**Shape 2 — artifacts render themselves (rejected).** Each `PlanningArtifact` kind owning its
own block rendering puts Slack knowledge in the kernel's contracts and gives five places for
the stage line, Back, and receipts to drift. That drift is precisely what the legacy agent
suffered from.

**Shape 3 — one live card updated in place over the whole session (rejected).** Hits
`msg_too_long` by stage 3 on a real day, and leaves no thread history — the user could not
scroll up to see what stage 1 concluded, which is the thing they asked for.

## Design

### Architecture

```
kernel (adaptive_timeboxing)
   │  (TurnOutcome, PlanningSessionSnapshot)
   ▼
stage mapper  slack_bot/stage_cards.py        pure: no Slack, no model
   │  StageCard
   ▼
renderer      timeboxing_cards.render_stage_card
   │  SlackBlockMessage
   ▼
Slack ◀──── card registry (chat_update previous card → receipt)

Slack press ──▶ ArtifactActionMeta ──▶ control table ──▶ TimeboxIntent ──▶ kernel
typed text  ──▶ interpreter        ──▶ same TimeboxIntent ───────────────▶ kernel
```

Three units, each answerable in one sentence:

- **Mapper** — *what* is on the card for this outcome. Depends on kernel contracts only.
- **Renderer** — *how* a card looks. Depends on `StageCard` only.
- **Control table** — *which intent* a drawn control means. Depends on `ArtifactActionMeta`
  and kernel intents only.

The kernel stays the authority on which stage a session is in (`_derive_target`,
`adaptive_timeboxing.py`) and which intents it accepts at a revision. Increment A gave it one
new capability: a real `GoBack` (`_go_back`, `adaptive_timeboxing.py`), and — after the
2026-09-03 live walk found "make the finances block 30 minutes" refused over the 4/5 card —
a `ReviseArtifact` honoured at every stage (#258): the intent names the current artifact of
any kind, the facts it carried and the instruction are filed, and the artifact is discarded
with what derives from it (`_discard`); a receipt reopens as before. A candidate is redrafted
from the same approved skeleton.

### Components

#### `StageCard` (new, `slack_bot/stage_cards.py`)

Strict Pydantic. Everything the renderer needs and nothing it does not.

```
StageLine       index: int (ge=1, le=5), name: str, next_action_label: str
ContextItem     text: str, source: Literal["memory","calendar","user","planner"]
DecidedItem     text: str, kind: Literal["assumption","fact"], ref: str
Asking          requirement_id: str, question: str, why_needed: str,
                options: list[BlockerOption]

# Controls are a discriminated union on `kind`, not one shape with an action
# name: each control carries exactly the binding its own press needs, so a
# renderer cannot draw a button whose value it has nothing to fill in.
ApproveControl  kind: "approve", artifact_id: str, artifact_revision: int,
                artifact_digest: str
DayTypeControl  kind: "day_type", user_id: str, channel_id: str, thread_ts: str,
                planned_date: str, tz_name: str
CommitControl   kind: "commit", candidate_id: str, calendar_id: str | None,
                day: str | None
UndoControl     kind: "undo", tx_id: str
BackControl     kind: "back"
CancelControl   kind: "cancel"
Control       = Annotated[Union[ApproveControl, DayTypeControl, CommitControl,
                                UndoControl, BackControl, CancelControl],
                          Field(discriminator="kind")]

StageCard       stage: StageLine
                session_key: str
                expected_revision: int          # press binding, per card
                context: list[ContextItem]
                decided: list[DecidedItem]
                asking: Asking | None
                body: str                       # the stage's own text
                controls: list[Control]
                done: str | None                # set only on a receipt
```

Artifact identity (`artifact_id`, `artifact_revision`, `artifact_digest`) is on
`ApproveControl`, not on the card: it binds the press, and a card that offers no approval has
no artifact to bind. `session_key` and `expected_revision` stay on the card, because every
control drawn from it carries both. `DecidedItem.steer` and `SteerControl` are not built; the
`ref` is what increment B will steer by.

`StageCard.as_receipt(done: str)` returns a copy with `asking=None`, `done` set, and every
control dropped **except `UndoControl`** — an undo names a write that reached the calendar and
outlives the card that announced it, while every other control asks the kernel to advance a
stage that has moved on. A receipt is the same model through the same renderer; there is no
second card type.

Increment A derives the stage from the turn outcome: `AwaitingApproval(planning_day)` → 1;
`AwaitingUser` → 1 when the pending blocker is a `DAY_FRAME` fact, otherwise 2; a skeleton → 3; a
candidate → 4; `Committed` → 5. Requirement ids and the readiness ladder are not renamed in A.
The kernel never mints a `day_frame` or `captured_inputs` artifact to read the stage from
(`_derive_target`, `adaptive_timeboxing.py`, ladders `SKELETON → VALIDATED_CANDIDATE →
COMMIT_RECEIPT` once the planning-day gate has passed); a `priorities` kind and a `day_frame`
reading of the calendar are B's work (see *Increments*).

`ref` on a `DecidedItem` is the assumption's or fact's own identifier (system-minted) so a
steer press names it exactly, never by text.

#### Payload models (`session_contracts.py`)

`DayFramePayload`, `CapturedInputsPayload`, `SkeletonPayload`, `CandidatePayload`,
`ReceiptPayload` were the target set; only `SkeletonPayload` is typed in A. `_planning_obligation`
(`harness_bridge.py`) hand-writes the skeleton shape into the brief as prose when the target
artifact is a skeleton — a restatement, not an imported schema; wiring the prompt to
`SkeletonPayload.model_json_schema()` so the two cannot drift (#158, import-not-copy) is still
open. A mapper validates `artifact.payload` into its model **before** reading a field: the
skeleton through `SkeletonPayload`, the planning day through `PlanningDay` (JSON mode — the
stored payload is a `model_dump(mode="json")` and the contracts are strict). This is the root
fix for #267: a skeleton payload that carries `blocks` and no `markdown` fails validation
loudly instead of rendering an empty card. Two payloads are not read that way yet. The
candidate goes through the `ValidatedTimeboxCandidate` dataclass, lenient on purpose so a
missing key is refused at the commit gate rather than inside a renderer; the commit receipt is
still read key by key (`committed`, `tx_id`, `durable`, `reason`), because `ReceiptPayload`
does not exist.

`SkeletonPayload` carries `markdown: str` (the `# anchor` / `-` outline) and `reasoning: str`
(the "talks it back" paragraph). No `blocks` field; blocks are the renderer's business.

The candidate payload stays free-form at submit because the host attaches
`rendered`/`snapshot`/`patch`/`digest` after the model returns (`_with_commit_basis`,
`deepseek_timebox_planner.py:231`); typing it is B's work, once the host-attached commit basis
has a home of its own.

`CapturedInputsPayload.open_questions` (#259) is deferred to B along with the `priorities`
artifact: the kernel mints no `captured_inputs` artifact in A, so there is nothing to carry them
on. In A, a planner's open question surfaces the same way any user-owned question does — as the
turn's `AwaitingUser` outcome, mapped straight to the card's `asking` field.

#### Mappers (`slack_bot/stage_cards.py`)

One function per kind, `map_<kind>(outcome, snapshot) -> StageCard`, plus a dispatch
`map_outcome(outcome, snapshot) -> StageCard`. Rules every mapper follows:

- `context` is built from the snapshot, not the artifact — but in A the only snapshot-derived
  context is the skeleton card's `reasoning` line. `applicable_constraints`, `calendar_snapshot`,
  and the `DAY_FRAME` fact feeding `context` on every card is B's work (stage 1 depth, #262,
  #260). The artifact is the planner's output; the snapshot is what the planner was *given*;
  showing the latter is what #262 and #260 ask for, once B wires it in.
- `decided` lists every `PlannerAssumption` not yet invalidated plus every user-supplied
  `PlanningFact` that bears on this stage.
- `controls` is computed from `(kind, snapshot.pending_blocker, snapshot.approvals)`:
  `proceed` when the kernel would accept `Advance`/`ApproveArtifact` at this revision; `back`
  when some earlier artifact is approved (never on stage 1); `cancel` always.
- Never a model call, never a string comparison on user text (CLAUDE.md). A mapper that would
  need to *judge* content has found a fact the planner should have recorded.

#### Renderer (`timeboxing_cards.render_stage_card`)

Header `*{index}/5 · {name}*`; a context section (one line per item, source as a leading
emoji); a decided section (each item one line, steer button on the right in B); the asking
section (buttons when `options` non-empty, otherwise a prompt naming the requirement); an
actions block from `controls` encoded with the existing `artifact_action_value`. A receipt
renders `done` in place of the actions block and drops `asking`.

`present_outcome` is the one function that turns a `TurnOutcome` into a Slack message and the
`StageCard` it was drawn from (`None` for the outcomes that are not stages). `render_outcome`
remains as a façade over `present_outcome(...)[0]` for the one caller that only needs the
message (`tests/unit/tmbx/test_commit_says_which_calendar.py`).
`render_date_card`, `render_skeleton`, `render_candidate`, `render_question` are deleted.

Length guard: context and decided sections truncate by *item count* with a trailing
"+N more" line. Text is never sliced.

#### Control table (`timeboxing_intents.py`)

`ArtifactActionMeta.decision` already admits `"back"`; the host never mapped it. The existing
decision → intent function gains `back → GoBack()`. Steer presses (B) map
`steer → ProvidePlanningFacts(facts=[PlanningFact(kind=meta.fact_kind, value=meta.value)])`,
which requires adding `fact_kind` and `value` to `ArtifactActionMeta` under a validator that
demands both together, the same way `choose_option` demands `requirement_id` and `option_id`.

No per-button handler. Every control decodes through the one table, and a test walks the table
against every drawn control.

#### Card registry (`slack_bot/stage_card_registry.py`)

The harness path stores no message ts for its cards; only the legacy proposal path does
(`proposal_message_ts` in `handlers.py`). A per-thread record
`{session_key: (channel_id, ts, artifact_id, artifact_revision)}` beside
`PendingTimeboxCandidates`. Host state only — the snapshot never learns a Slack ts.

#### Kernel: `GoBack`

`GoBack` (`_go_back`, `adaptive_timeboxing.py`) is the ladder as built, top match wins, never
past a commit: the session holds a `COMMIT_RECEIPT` saying `committed` (or reads `committed`)
→ `TurnFailed(code="session_committed")` — the receipt and not the status alone, because a
revision reopens a committed session and `_invalidate` keeps its receipt, so a status-only
guard let Back walk a day that is already on the calendar; any other non-`open` status
(cancelled) → `TurnFailed(code="session_cancelled")`; a candidate exists →
invalidate the skeleton (the run loop re-presents it); a skeleton exists → invalidate captured
inputs and re-ask `skeleton.requested_activity`; the planning day is set → clear it (the
planning-day gate re-presents the existing day artifact); nothing of the above →
`TurnFailed(code="nothing_to_go_back_to")`. Facts are kept throughout — back is not forget.
`ReviseArtifact` against a skeleton or candidate files a `REVISION_INSTRUCTION` fact naming
that artifact, files any facts the intent carried, and discards the artifact with its
descendants — the approved skeleton under a revised candidate stands. Discarding an artifact
retires the planner assumptions made to produce it (`TimeboxRequirements.target_of`), so the
Decided list does not grow by one placement assumption per redraft.

#### Steering facts (increment B)

Three new `FactKind` values — `SUSPENDED_CONSTRAINT`, `DENIED_ASSUMPTION`,
`DAY_SPECIFIC_CONSTRAINT` — plus `ONE_THING` for stage 2. All are `PlanningFact`s filed by
`ProvidePlanningFacts`, so they invalidate `captured_inputs` and everything downstream for the
same reason a typed fact does. Superseded, never deleted: reverting a suspension is a new fact
that names the earlier one. The brief reflects the current set by construction because the
brief is built from the snapshot.

The memory server is touched only when the user says the change is permanent ("always"). That
is a `PendingBlocker` with two options — *this day* / *always* — and only the second calls
`memory_observe`. Nothing writes to memory from a steer press alone.

### Data flow — one turn

1. Press → `ArtifactActionMeta` → control table → `TimeboxIntent`. Typed text → interpreter →
   the same `TimeboxIntent`. Unchanged.
2. Kernel applies the intent → `(outcome, snapshot)`.
3. Registry lookup for `session_key`. If a card is registered at a *different* message than
   the one this turn is drawing into, the registered card — the `StageCard` as it was shown,
   kept by the registry rather than re-derived — is `as_receipt(done=…)`d and `chat_update`d
   in place. A receipt drawn from the wrong state would be a lie; a missing one is cosmetic,
   so the edit is best-effort and never fails the turn.
4. Mapper builds the new `StageCard`; renderer posts it; registry records the new ts.
5. `GoBack` follows the same path: step 3 rewrites the later stage's card to a receipt whose
   `done` reads "reopened"; step 4 re-posts the earlier stage live.

Typed day change (#265): the kernel already invalidates everything; step 3 turns the stale
stage-1 card into a receipt reading "superseded", step 4 posts the fresh one. The thread root
is relabelled by the existing root-relabel path.

### Error handling

| Case | Behaviour |
|---|---|
| Stale press (revision mismatch) | Refused with the existing generic copy (`TIMEBOX_FAILURE_TEXTS`, `timeboxing_cards.py`); A does not name the stage in the refusal — that needs the failure card to know the stage, which is B's work alongside the steer controls. |
| Payload fails validation | `ValidationError` logged at error with artifact id and kind; the failure card is rendered. Never a blank card. |
| `chat_update` of the receipt fails | Logged; the new card is still posted. A live card is never held hostage by a cosmetic edit. |
| Back on stage 1 / while a blocker is open | Control not drawn. If the intent arrives anyway (typed), kernel returns `TurnFailed(code="nothing_to_go_back_to")` and the host renders it. |
| Planner emits `open_questions` | Surfaced in `asking` (or as a planner context line when several). Never dropped. |
| Steer press on an item the snapshot no longer holds | `ref` does not resolve → refused with the stale-press message. |

### Testing

Unit tests opt in to the harness backend the way `test_timebox_session_surface.py` does
(`tests/conftest.py` pins legacy by default).

- **Mappers** — one fixture snapshot per kind; assert `stage.index`, presence and `source` of
  context items, `decided` refs, and the exact `controls` list. Break each on purpose once.
- **Renderer** — golden tests on block *structure*: header text, action ids, section count.
  Never on prose.
- **Control table round-trip** — for every `StageCard` a mapper can produce, every drawn
  control decodes to an intent the kernel accepts at that revision (drive the kernel with the
  decoded intent and assert it is not `TurnFailed(invalid_intent|unsupported_intent)`).
- **Kernel `GoBack`** — one test per rung of the ladder: a written day (a `COMMIT_RECEIPT`, or
  a `committed` status) refuses with `session_committed` and keeps its `planning_day`, before
  and after a reopen; a cancelled session refuses with `session_cancelled`; a candidate is
  dropped and the skeleton re-presented, with the skeleton's approval withdrawn; a skeleton is
  dropped and `skeleton.requested_activity` re-asked, with the facts kept; a set `planning_day`
  is cleared and its approval withdrawn; nothing left refuses with `nothing_to_go_back_to`.
- **Registry / transition** — old card becomes a receipt, new card posted, ts recorded; the
  `chat_update` failure path posts the new card regardless.
- **Payload models** — the 2026-09-02 skeleton payload (`blocks`, no `markdown`) fails
  validation (#267 regression).
- **AST guard** — `stage_cards.py` imports nothing from `slack_sdk`, and nothing under
  `fateforger.slack_bot` that renders or routes (`handlers`, `timeboxing_cards`,
  `timeboxing_commit`) — an import from any of those would drag a Slack client in. What it does
  import from `slack_bot` is `timebox_candidate`: the mapper arms the pending candidate as it
  draws the stage-4 card, so the button it renders and the entry the commit gate spends are
  minted together. That module is pure host state with no client of its own.
- **E2e** — the five-stage walk is covered in A by `tests/replay` and
  `tests/unit/test_stage_receipts_in_the_turn.py`. The e2e walk with a Back press, extending
  `tests/e2e/test_slack_timebox_command.py` to assert the receipt edit and the re-posted card,
  lands with B, when stage 2 is a real card rather than a question.

No eval tests: nothing here asks a model a new question. Stage-2 prompt work (B) will need
them and says so in its own plan.

### Increments

**A — breadth.** Resolves #264, #265, #267.
`StageCard`, renderer, mappers for all kinds, receipts, `GoBack` in the kernel, `SkeletonPayload`,
card registry, control-table `back`. Stage 2 is a thin "2/5 Priorities" card built straight from
the `AwaitingUser` turn outcome, not from a `captured_inputs` artifact the kernel does not mint:
`decided` = requested activities, an empty "from your board" section, Proceed/Back/Cancel. No
drill-down yet.

**B — depth.** Stage 1 first (#262, #260): context section fed from `applicable_constraints`
and `calendar_snapshot`, per-item steer controls, the "this day / always" blocker, the
evocation prompt. Then stage 2 (#261, #259, #263): a `priorities` artifact, the focusing
question, one thing + two conditional, AVBD check, windowed/floating.

Each increment is its own implementation plan.

## Open questions

- A `captured_inputs` gate — refusing to plan the skeleton until stage 2 has been shown — is
  deferred to B.

## Out of scope

- Notion and TickTick as a task backend (own grilling ticket, map C). Stage 2's "from your
  board" section is the consumer and stays empty until then.
- A revision's *quality* loop at stage 4 (diffing the redraft against the previous candidate,
  steering by block); the kernel semantics landed in increment A.
- Task marshalling as a session.
- Deleting the legacy agent (`2026-08-24-harvest-then-delete…` spec).

## Files

New: `src/fateforger/slack_bot/stage_cards.py`, `src/fateforger/slack_bot/stage_card_registry.py`,
`tests/unit/test_stage_cards.py`, `tests/unit/test_render_stage_card.py`,
`tests/unit/test_stage_card_registry.py`, `tests/unit/test_stage_receipts_in_the_turn.py`,
`tests/unit/test_back_press_reaches_the_kernel.py`. `GoBack`'s kernel tests were appended to the
existing `tests/unit/test_adaptive_timeboxing.py` rather than a new file.
Changed: `session_contracts.py` (`SkeletonPayload`), `adaptive_timeboxing.py`
(`GoBack`/`_go_back`), `timeboxing_cards.py` (`render_stage_card`, `present_outcome`,
`render_outcome`), `timeboxing_intents.py` (control table), `handlers.py` (transition step),
`harness_bridge.py` (`_planning_obligation` states the skeleton payload shape in the brief).
Deleted: `render_date_card`, `render_skeleton`, `render_candidate`, `render_question`.
