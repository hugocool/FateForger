# Asked ≠ started — a question to the Schedular is answered, never turned into a session

**Date:** 2026-09-05
**Status:** Approved (design, Hugo, 2026-09-05); tickets charted on map #157
**Extends:** `2026-09-03-planning-card-reply-seam-design.md` (the seam), `docs/architecture/proposal_object_contract.md` (the contract)
**Map:** #157 *Rebuild FateForger on the four-move architecture*

## The incident

03:43, 2026-09-05. Under the Admonisher's planning card (draft, "Not added yet"), Hugo typed
*"Is it planned?"*. The card's interpreter read it correctly — `decision: none`, a question, not a
press (`logs/llm_io_20260905_010643_9295.jsonl`, record 3). The reply was then routed to
`timeboxing_agent`, which opened a fresh five-stage session and posted the Stage-1 day-confirm
card. No model was consulted for that: the card is minted deterministically.

Two faults, one visible and one underneath.

1. **Routing.** Every DM turn pins `set_user_focus`, and the resolver only asked
   `planning.owns_thread` when focus had *not* already chosen timeboxing. Planning was the last
   resolver, not the first. Fixed in #310.
2. **The Schedular has no door that does not start a session.** `derive_timebox_intent` returns
   `StartSession()` for any text before a day is locked, *by design, with no model asked* — its
   docstring says "there is nothing to decide about". A committed session offers only
   `provide_facts` and `revise`, and `InterpretedTimeboxTurn` has no `none`, so a question to
   the agent that just planned the day is coerced into a fact or a revision. #287 (one-off
   instructions filed as durable MUST rules) is the memory-side echo of that coercion.

Hugo's expectation, stated: *either the timeboxing agent that just did the session answers, or
the Schedular that can pull my calendar sees there is no planning session and says so.* Both
wear the same "The Schedular" persona (`planner_agent` and `timeboxing_agent`,
`workspace.py:62-71`).

## What already exists, and is kept

- The receptionist prompt already routes *"is a session planned?"* to `planner_agent` for
  calendar inspection (`receptionist/agent.py:31`). With #310 the planning-card thread reaches
  it. **No change to the receptionist's routing.**
- `planner_agent` (`agents/schedular/agent.py`) holds the calendar MCP tools and answers in
  prose (`reflect_on_tool_use=True`, #180). It is the answerer.
- The shared surface interpreter (`surface_intents.py`) and per-state `allowed_decisions` in
  `timeboxing_intents._display_context`. The change is one more decision per state, not a new
  interpreter.

## Decisions (Hugo, 2026-09-05)

- **S2 — a question is a kernel outcome.** `AskQuestion` joins `TimeboxIntent`; `kernel.turn`
  returns `Asked` with the snapshot untouched; the host renders `Asked` by asking
  `planner_agent`. The kernel stays pure and the single dispatcher; no host-side second
  dispatcher.
- **N1 — the no-day short-circuit becomes a judged state.** `_display_context` gains
  `no_session` with `("start", "question", "cancel")`. `StartSession` becomes an interpreter
  decision like every other. `cancel` rides along: it is #299's option 3 for one tuple entry.
- **A1 — `planner_agent` answers**, given the session described plus the user's words. Not a
  harness turn (revisit under #302), not a raw LLM call (a second calendar answerer that never
  compounds).
- **D1 — `describe(snapshot)` is derived from `StageCard`**, the model `stage_cards.py` already
  builds from a snapshot. One derivation, two renderers (Block Kit, prose).
- **F1 — focus demotes to `receptionist_agent`**, not the channel default, closing #310's
  review note. The general rule (F2: focus never applies inside a bot-posted thread) is written
  into the contract; F1 is what ships.

## Section 1 — the rule, in the contract

Two sentences added to `docs/architecture/proposal_object_contract.md` §7:

> A thread whose root a surface posted belongs to that surface. User focus — the DM-wide
> memory of who last answered — never outranks that ownership; a new surface registers its
> root in the resolver chain (`handlers.route_slack_event`, the ordered resolvers before agent
> selection) or its threads will be routed by focus.

> An agent that owns a workflow exposes `question` in every state its surface allows. Asked is
> not started, and asked is not revised: a question changes nothing in the session it is asked
> of.

And the F1 change in `handlers.py`: when planning owns the thread and `timeboxing_agent` came
only from sticky `user_focus`, `agent_type` falls back to `receptionist_agent` (today: the
channel default, which is a no-op when that default is itself `timeboxing_agent`).

## Section 2 — `AskQuestion` → `Asked`

**Intent.** `AskQuestion(kind="ask_question", question: str)` in `session_contracts.py`, added
to the `TimeboxIntent` union. `question` is the user's words verbatim — the host binds it from
the Slack text, never from the model's output, so nothing the model wrote reaches the
answerer as if the user said it.

**Outcome.** `Asked(kind="asked", question: str)` in the `TurnOutcome` union. The kernel returns
it without touching the snapshot: **no artifact, no fact, no assumption, no invalidation, and
the revision does not advance.** Whether the outcome is recorded in the session's outcome
envelope is the implementer's call; the invariant is that a subsequent `load` sees the same
snapshot revision as before the question.

**Interpreter.** `InterpretedTimeboxTurn` gains `"question"` (and, for the `no_session` state,
`"start"`). `_display_context` adds `"question"` to every state's tuple — `no_session`,
`planning_day`, `skeleton`, `candidate`, `refine`, `committed`. The binding in `timeboxing_intents`
maps `question` → `AskQuestion(question=user_text)` and `start` → `StartSession()`.

**Prompt fragment.** One paragraph on the timeboxing surface's fragment: a reply that asks about
the day, the plan, the calendar, or what was decided is `question`; a reply that supplies a
fact, a correction, or an instruction against the plan is what it was before. A question that
also carries a fact is a fact — the fact changes the day, the question does not.

**Cancelled sessions.** `_display_context` returns `()` for `cancelled` and the interpreter
raises "does not accept another intent". That stays: a cancelled thread is closed. `question`
is not added there.

## Section 3 — the host answers

In `_run_adaptive_timebox_turn`, an `Asked` outcome is rendered by:

1. `describe(snapshot)` — a prose rendering of the `StageCard` for the current snapshot: the
   planning day and its type, the stage, what is decided (facts and assumptions, by owner), the
   open question if any, and for a committed session the receipt (block count, calendar,
   `tx_id`). Same fields the card shows; nothing the card does not show.
2. `runtime.send_message(TextMessage(content=f"{describe}\n\nThe user's question:\n{question}"),
   recipient=AgentId("planner_agent", key=session_key))` — the same call shape the handoff path
   uses. `planner_agent` reads the calendar if it needs to and answers in prose.
3. The progress card ("thinking…") becomes the answer, in-thread, under the Schedular persona.
   No stage card is re-rendered; the session's card is exactly as it was.
4. Failure stays loud: the answerer raising or timing out is reported in-thread as the
   `TurnFailed` copy is, and metered `record_error(component="surface_intent",
   error_type="answer_failure")`. Never degraded to "no answer" silently; never retried into a
   session start.

Reversibility (map #157's second constraint): a question writes nothing, so there is nothing to
revert. The one delivery — the answer message — is a Slack post, edit-in-place only, like every
other reply.

## Section 4 — the `no_session` state

`derive_timebox_intent` no longer returns `StartSession()` unconditionally when no planning day
exists and no `PLANNING_DAY` artifact is pending. Instead `_display_context` returns
`("no_session", ("start", "question", "cancel"), None)` for that case and the interpreter is
asked, with `display_state="no_session"` and no offered options. The docstring's claim that
"there is nothing to decide about" is deleted with the code that made it true.

`start` → `StartSession()`, the same object as before: the opening turn is unchanged.
`question` → `AskQuestion` → `Asked` → the host answers; **no session row is created at all: the
kernel answers a question over an in-memory snapshot, and a cancel with nothing to cancel is
refused before any row exists**, and revision stays 0.
`cancel` → `CancelSession()` before a day is locked (#299 option 3). A cancel that has nothing to
cancel — no row, or a row with no locked day and no artifacts — is refused as `TurnFailed` with
code `nothing_to_cancel`.

The empty-text case (`Advance()`) is unchanged.

## Section 5 — tests and evals

**Unit — model stubbed, prove the plumbing.**

- Interpreter stub returns `question` in each state → `kernel.turn` returns `Asked`; the
  snapshot revision is unchanged after the turn; no artifact or fact was added.
- `Asked` → the host sends `planner_agent` one message containing the described snapshot and
  the user's verbatim words; the reply text is posted in-thread; no stage card re-render.
- `no_session` + `question` → revision stays 0, no `StartSession` turn ran.
- `no_session` + `start` → identical outcome to today's short-circuit (pin by snapshot equality).
- `no_session` + `cancel` → `CancelSession` reaches the kernel.
- Answerer raises → in-thread failure line, `record_error` called, no session started.
- `describe(snapshot)` for a committed session names the receipt; for stage 3 names the
  skeleton's decided items. Assert fields, not sentences.
- F1: planning owns the thread, `user_focus` is `timeboxing_agent`, channel default is
  `timeboxing_agent` → routed to `receptionist_agent`.
- An AST guard: `derive_timebox_intent` contains no unconditional `StartSession()` return.

**Eval — real model, `@slow`, n=8, threshold 7, no temperature pin** (the seam spec's pattern,
`tests/integration/test_eval_planning_card_intent.py`'s `SAMPLES`/`gather` shape):

- **No case text appears verbatim in `QUESTION_PARAGRAPH`.** A case whose exact words are quoted
  in the prompt measures recall of the prompt, not the judgement the prompt is meant to produce,
  so every text the paragraph quotes is reworded to the same intent in other words (#319).
- `no_session`: question-vs-start-vs-cancel. Questions: *"has it been scheduled?"*, *"did you put
  the gym in?"*, *"what's on my calendar tomorrow?"*, *"is there a planning session today?"*.
  Starts: *"plan my day tomorrow"*, *"let's timebox saturday"*, *"kick it off"*, *"right, let's
  begin"*. Cancels: *"cancel this"*, *"never mind, not today"*.
- `committed`: question-vs-facts-vs-revise. Questions: *"what did we decide about lunch?"*,
  *"when's the deep-work block?"*. Facts: *"I sleep 00:30–08:30"* plus the two mixed
  ask-and-supply texts below, which carry the positive half of the break-it check. Revise:
  *"move the work two hours later"*.
- **A draw is retried once, and only when its exception carries a transport cause.** A draw that
  reached a *wrong* decision — one outside `allowed_decisions`, output that does not fit the
  narrowed schema, a binder refusal — is never retried: it is the measurement. A blind
  `except Exception` here re-rolls the exact degenerate answer a stripped paragraph is supposed
  to produce, which would hide the break-it result (#319). The endpoint's own failure rate is
  #325's problem, reported per case and never asserted on.
- Break-it check, as the seam eval does: strip the prompt fragment's question paragraph and
  confirm the discrimination collapses. **Measured 2026-09-05 (#319), it takes two families, and
  a plain interrogative is neither of them:** stripping the paragraph does not move a pure
  question at all — *"is it planned?"* and *"what did we settle on for lunch?"* still answer
  `question` at 7/8 and 8/8 without it, because the `question` label in `allowed_decisions`
  already carries them, so asserting on those tested the label. What does move:
  - **fresh session, asked becomes started** — *"what's on my calendar tomorrow?"* answers
    `AskQuestion` 8/8 with the paragraph and `StartSession` 8/8 without it (6/8, 6/8 and 7/8 to
    `StartSession` on three earlier stripped draws, and `StartSession` never once in an
    unstripped run). That is the regression this branch is named for, reproduced on demand.
  - **committed session, the fact lost to the question** — *"did you move lunch? I sleep
    00:30–08:30"* answers `ProvidePlanningFacts` 8/8 with the paragraph and 1/8 without it
    (*"is deep work still at 9? also I get up at 07:00"*: 8/8 against 0/8).

  Both assert the **flip** — the wrong decision outnumbering the right one — not the absence of
  the right one. An absence-based bar is cleared by two lost calls with the paragraph doing
  nothing, which is how the first version of this check "passed"; and a bar of
  `StartSession >= 7` would have failed two of three honest stripped runs at 6/8. A lost draw
  subtracts from both counts and can never manufacture a flip. A discriminator that passes
  without its discriminating sentence is not one — and it has to be aimed at a case that needs
  discriminating.

Each case is sampled 8 times concurrently and asserted on the count. A prompt fix validated by
one passing call has not been validated (CLAUDE.md).

## What this does not do

- The admonisher's own router — #164, with the harness port.
- Re-keying the DM session under one-session-per-thread — #302. The `{channel}:dm` key stays;
  a question in a DM asks the DM's session, which is the one that just ran.
- Renaming the two Schedulars. Which one answered is logged, not shown.
- F2 in code. The rule is written; the code change waits until DM session threads are
  verifiable.
- The receptionist's `"?" in message.content` follow-up heuristic — a CLAUDE.md violation on
  this path, filed as its own issue, not folded in.

## Tickets (map #157, all `wayfinder:task`, AFK)

| | Ticket | Blocked by |
|---|---|---|
| A | `AskQuestion` intent, `Asked` outcome, `question` in every `_display_context` state, prompt fragment | — |
| B | `describe(snapshot)` from `StageCard`; host renders `Asked` via `planner_agent`; failure loud | A |
| C | `no_session` state replaces the `StartSession()` short-circuit; `cancel` rides along | A |
| D | Eval: question-vs-start, question-vs-facts, break-it check | A, C |
| E | F1 focus demotion; the two rules in the contract doc | — |

Waves: A ∥ E → B ∥ C → D. One worktree, one branch (`feat/asked-not-started`), subagents do not
commit; the controller commits between waves.

## Related

#310 (the routing half), #281 (the seam), #299 (cancel before a day), #287 (revision
instructions as rules), #302 (one session per thread), #164 (admonisher router), #180
(planner answers in prose), #88 (surface contract rollout).
