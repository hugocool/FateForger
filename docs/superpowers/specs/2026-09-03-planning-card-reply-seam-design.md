# A reply on a proposal thread is read against the proposal

Design for the 2026-09-03 planning-card incident and the general mechanism behind it.
Increment one of #88. The proposal-time policy (what the card offers and when) is #271 and
not part of this.

## The incident

At 10:32 the Admonisher posted the daily planning card in Hugo's DM: a time picker, an
*Add to calendar* button, and the line *"Still no planning session. Choose a time now — this
is your daily anchor."* At 10:34 Hugo replied **"Okay!"**. The bot answered with a cold
onboarding menu — *"Standing by. Heroism is built step by step. What is your top priority
today?"* — under the label `*admonisher_agent*`, with `**bold**` markdown Slack does not render.

The journal (`llm_io_20260902_080757_9470.jsonl`, records 5–6) shows the sequence:

1. `planning_thread_reply_interpreter` read "Okay!" and answered `ignore` at 0.9 — correctly,
   for the question it was asked: it had never been shown the card's controls, so it could
   not know that "Okay" was one of them.
2. `maybe_handle_thread_reply` turned `ignore` into `False`, and the caller read `False` as
   *"not a planning thread"* — four lines after `_resolve_thread_draft` had proved it was one.
3. The message fell into ordinary routing. Hugo's *user focus* was still `admonisher_agent`
   from a DM at 08:22 (a one-hour TTL), so a fresh, per-thread `AssistantAgent` with a
   381-token prompt — the persona plus the word "Okay!" — answered the only way it could.

Three facts an incoming reader would otherwise assume wrongly:

- The admonisher was not the fallback by design. Routing is thread focus → user focus →
  channel default → `receptionist_agent`. Any agent Hugo had spoken to within the hour would
  have caught the message, cold.
- The interpreter's exception path returns the same `should_handle=False`. A model outage
  produced the identical cold menu, silently.
- Nothing durable remembers which threads belong to which surface. `FocusManager` is an
  in-memory TTL cache; the bot restarted at 11:50 and 12:03 today. A DM timeboxing thread
  replied to after a restart has the same hole.

## Decisions Hugo made

- **Confirmation is the press.** "Okay", "yes", "sure" on a proposal card are the primary
  control in words. "No, let's do 13:45" and a bare "13:45" both mean: set the time *and* add.
- **A reply that is none of the controls still gets an agentic answer**, but it is low
  priority. What matters is that the answerer cannot be cold.
- **One shared seam and one shared interpreter.** The planning card and the timeboxing stage
  cards use the same controls-aware interpreter; the surfaces keep their own typed decisions.
- **No `temperature=0`.** The pin on the timeboxing interpreter client goes, in its own commit.
- **Base branch** is `fix/post-mortem-2026-09-02`; merge order is that PR first, then this.
- **One bot.** The single-bot guarantee is `demo.py`'s job (#245, held by another session),
  not a step in a checklist.

## Section 1 — the seam

One step in `handlers.route_slack_event`, before any agent routing, replacing the bool
interception at `handlers.py:2802`:

```
resolve_surface(channel, thread_ts)          # durable state, never FocusManager
  none        → existing routing, unchanged
  surface S   → S.interpret(text) →
      Press(control)   → S.press(control)     # the same executor the button calls
      NoPress          → existing routing, with S.describe() prefixed to the text
      raises           → in-thread failure line + record_error; never routes
```

**Resolution is from stores.** Planning: the draft store by `(channel, message_ts)`, with the
existing thread-root fallback. Timeboxing: the session store by thread key, which needs a
non-creating `load` beside `load_or_create` — one method. Resolvers are an ordered list;
first hit wins. `FocusManager` keeps its job for everything that is not a surface thread.

**`NoPress` routes with context.** Nothing new decides *who* answers. Whatever the existing
precedence picks receives `S.describe()` — title, proposed time, status, the controls on
offer — ahead of the user's words, so it cannot open with a menu. Replies on the plain-text
path go through `mrkdwn.to_mrkdwn`, and `_with_agent_attribution` stops prepending the agent
id to text replies (the block-kit context footer stays).

**Failure is a third outcome.** The interpreter raising is reported in the thread and in
`fateforger_errors_total{component="surface_intent"}`. It never becomes a routed message.

**Timeboxing goes behind the seam too.** Its focus binding and `_auto_recover` stay for the
channel case; the seam is what catches the DM thread after a restart. The kernel turn,
`derive_timebox_intent`, and the stage-card renderer do not change.

Out of scope: task cards and constraint review (#88 backlog), the proposal-time policy
(#271), the admonisher itself (#164).

## Section 2 — the shared interpreter

Extract the generic half of `slack_bot/timeboxing_intents.py` into `slack_bot/surface_intents.py`.
Generic is *choosing among offered things*; surface-specific is *what else the reply carries*.

**`SurfaceView`** — built by the surface from its own durable state:

| field | meaning |
|---|---|
| `surface_kind` | `planning_card` / `timebox_session`; used for attribution and the prompt fragment |
| `display_state` | the stage or status name the surface is in |
| `allowed_decisions` | the decisions this state accepts, always including `none` |
| `offered_controls` | `[{control_id, label, effect}]` — ids minted by the host |
| `open_question` | optional; what the surface is currently asking |
| `context` | surface-specific facts the model needs (timeboxing: proposed day) |

**Schema.** `InterpretedTurn(decision: Literal[allowed…], control_id: Literal[offered…] | None)`,
narrowed per turn with `create_model` — today's `_turn_schema`, moved. The surface supplies
extra fields: timeboxing keeps `facts`, `revision_instruction`, `day_type`, `day_offset`;
planning adds `selected_time: Clock | None` (the existing `Clock` validator).

**Prompt.** A generic preamble — choose only a listed decision; when the user picked an
offered control, answer its id exactly; never invent identifiers; the host owns the
calendar — plus a surface fragment. Timeboxing's fact rules move verbatim. Planning's
fragment is two lines: normalise clock times to 24h `HH:MM`; set `selected_time` only when
the user states one.

**Binding stays per surface.** The interpreter returns the schema instance. Timeboxing's
`_intent_from_interpreted` is untouched. Planning gets a binder:

| decision | bound intent |
|---|---|
| `add` | `Press(add_to_calendar)` |
| `update_time` | `Press(set_time(selected_time))` — raises without a time |
| `update_time_and_add` | `Press(set_time(t))` then `Press(add_to_calendar)` — raises without a time |
| `retry` | `Press(retry)` |
| `none` | `NoPress` |

**Planning `SurfaceView` from `DraftStatus`:**

| status | controls offered | decisions |
|---|---|---|
| `DRAFT` | *Add to calendar* (primary; effect: add the session at the shown time), *time picker* | `add`, `update_time`, `update_time_and_add`, `none` |
| `FAILURE` | *Try again*, *time picker* | `retry`, `update_time`, `update_time_and_add`, `none` |
| `PENDING` | — | `none` (the seam answers "still adding…", as today) |
| `SUCCESS` | — | `none` (the seam answers "already on your calendar", as today) |

*Edit* opens a modal for duration and date; it is not addressable by text and is not offered.

"Okay" becomes a press through the offered-controls context, not a rule in code: the primary
control's `effect` says what pressing it does. Whether the live model reads it that way is
what Section 3 measures.

**Attribution.** `llm_attribution(agent=f"{surface_kind}_intent_interpreter")`. Timeboxing
keeps `timebox_intent_interpreter` / `timebox_intent` so its metrics do not move.

**The pin.** `_build_timeboxing_intent_interpreter` passes `temperature=0`; CLAUDE.md retired
that on measurement. The shared client builder does not pin. Own commit; the check is the
timeboxing eval (`tests/integration/test_eval_day_frame.py`), whose `_client()` pins too and
loses it in the same commit.

## Section 3 — tests and evals

### Unit — model stubbed, prove the plumbing

Seam, in the style of `tests/unit/test_slack_timeboxing_routing.py`:

- planning thread, interpreter → `none`: no press; the runtime receives the text **prefixed
  with the card summary** (assert title, time, and control labels are in it)
- planning thread, interpreter → `add`: `start_add_to_calendar` runs; `runtime.calls == []`
- planning thread, interpreter raises: failure line in the thread, `record_error` called,
  `runtime.calls == []`
- a thread that is no surface: routing byte-identical to today
- a DM timeboxing thread with an empty `FocusManager`: the session-store resolver finds it,
  the kernel turn runs, the receptionist is never called
- `test_route_slack_event_uses_planning_thread_reply_handler_before_runtime` moves to the
  tri-state return

Interpreter core: the offered controls appear as the `Literal`; a decision outside
`allowed_decisions` raises; a `control_id` that was not offered fails validation. No
heuristic fallback anywhere. `tests/unit/test_timeboxing_intents.py` passes unchanged — the
extraction is behaviour-preserving and that suite is the proof.

Planning binder: `update_time_and_add` without a time raises. Parity: the text press and the
button both reach `start_add_to_calendar` (extend
`test_thread_reply_update_and_commit_uses_same_add_to_calendar_path`).

Formatting: `to_mrkdwn` is applied on the plain-text reply path; the posted text no longer
starts with `*<agent_type>*`.

Guard: an AST test that `surface_intents.py` imports no `re` and performs no substring or
equality test against the user's text.

### Eval — real model, `@slow`, n=8, threshold 7, no temperature pin

Runs on the client production uses. `OPENROUTER_DEFAULT_MODEL_FLASH` still pins
`google/gemini-3-flash-preview` where CLAUDE.md says `3.6-flash`; flagged, not changed here.

Against a `DRAFT` card proposing 10:38:

| reply | expected |
|---|---|
| "Okay!", "yes", "sure, do it" | `add` |
| "no, let's do 13:45" | `update_time_and_add`, `13:45` |
| "13:45" | `update_time_and_add`, `13:45` |
| "make it 17:00 but don't add yet" | `update_time`, `17:00` |
| "why 10:38?", "plan tomorrow for me", "later" | `none` |
| "try again" on a `FAILURE` card | `retry` |

**Break-it check.** Delete the primary control's `effect` text and confirm "Okay!" drops below
threshold. That is the proof that the controls context, not luck, makes "Okay" a press. A
test that passes the first time has not yet earned trust.

Timeboxing regression: `test_eval_day_frame.py` re-run after the pin drop.

## Section 4 — delivery

1. **Branch.** Worktree `.worktrees/planning-card-reply-seam` from `fix/post-mortem-2026-09-02`.
   TDD per Section 3; full unit suite green; both evals run and their rates in the PR body.
2. **Merge order.** The post-mortem PR reaches `origin/main` first (its owner resolves the #221
   three-line fixture rename in `tests/unit/test_planning_reminder_suppression.py`). This
   branch then rebases onto `main`, opens its PR, merges. The owning session is told before
   any push and when the PR is up, so the order is theirs → mine, never a race.
3. **Restart from `main`.** From the parent checkout on the merged sha with a clean tree:
   `scripts/demo.py status` → `demo.py start` → `status`, reading the working-tree line, each
   process's `serving` line, and `profile … loads`. No `DEMO_MEMORY_DB` from the parent; it is
   a worktree-only override. Since #245 (`3437ead`) `demo.py start` stops every other Slack
   bot before starting one and `status` exits non-zero if a foreign bot is present, so the
   single-bot guarantee is the script's, not the checklist's. What the script only reports and
   does not enforce: `demo.py` starts from the working tree, not HEAD — the tree has to be
   clean, not just the checkout. Coordinated with the sessions holding the stack before it
   happens.
4. **E2E in Slack**, Hugo's DM, planning card only:
   - fresh card via `scripts/dev/force_nudge.py`
   - **"Okay!"** → card shows *Adding…* then *Added*; journal shows
     `planning_card_intent_interpreter` → `add` and no `admonisher_agent` call
   - fresh card, **"no, let's do 13:45"** → added at 13:45
   - **"why 10:38?"** → a contextual answer; no menu, no `*admonisher_agent*`, no `**bold**`
   - "Okay!" writes a real event (today's session, deterministic id `ffplanning…`, never the
     2026-09-04 day another session is planning); Hugo is told before it is sent.
5. **Rollback.** `demo.py` restart on the previous `main` sha. No migration; the store is
   untouched.

## What this does not do

- Change what the card proposes or when (#271).
- Give the admonisher a router. Its future is #164; the seam makes every agent context-aware
  on surface threads, which is what the incident actually needed.
- Migrate task cards or constraint review. They plug into the seam under #88 once their
  regex parsing has been deleted.
- Fix the `OPENROUTER_DEFAULT_MODEL_FLASH` pin.

## Related

#88 (contract rollout), #271 (proposal-time policy), #245 (single bot), #164, #165, #256.
Contract: `docs/architecture/proposal_object_contract.md` — this adds the clause it lacked:
what happens to a reply on a proposal thread after `ignore`.
