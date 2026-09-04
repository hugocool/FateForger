# The planning session starts itself, and the Admonisher walks you to it

Design for what happens when the daily planning event's start time arrives on the calendar.
Decided with Hugo on 2026-09-04. Increment A of the admonishment loop; the harness port
(#164) lifts its policy later.

## Today

Nothing fires at the planning event's start. The reconciler (`haunt/reconcile.py`) has one
rule — *is there a planning event in the next 24 hours?* — and once the card was added it is
satisfied and silent. Sessions open only when Hugo types. `HauntingService` can schedule
escalating follow-ups with cancel-on-reply, but nothing arms one for planning, and a Slack
reply never cancels one (only an agent tool call or a message whose sender is the user
channel does).

An event that has *passed* with nothing planned still satisfies the rule: `evaluate` returns
`[]` on `anchor_match` whether the event is ahead or two hours gone.

## Decisions Hugo made

- **At the event's start the agent starts, it does not ask.** Session root and a pre-warmed
  day card appear; that is the first nudge.
- **The user is told it started**: one DM with the thread link. That message anchors the ladder.
- **Ladder at +2, +5, +10, +20, +40 minutes** after the open; any reply or press in the session
  thread cancels the rest; commit and cancel cancel it too.
- **Which day**: before 14:00 in the event's timezone plans the event's day; later plans the
  next day. Host arithmetic. #282 (the event records its planned day) replaces the rule later.
- **When the slot is over and nothing was planned, the planning session is missing again**:
  the reconciler's ordinary missing-planning ladder starts, proposing a new slot (#271).
- **Home**: the current haunt stack now, with every policy value as data; the harness port
  (#164) lifts it. Two facts found on 2026-09-04 make this the only honest order: the harness
  `schedule` package is session-local ("no external notification channel or cold-session
  scheduler exists"), and the `tmbx` profile has no Slack mount.

## Section 1 — Trigger and guard

**Two new jobs from the reconciler.** `PlanningReconciler.evaluate` already reads the anchor
event. On the `anchor_match` branch — the only branch that knows the event's start — it emits,
when `start > now`:

- `JobKey("rule", planning_rule, user, window_start, "session_start")` at the event's start;
- `JobKey("rule", planning_rule, user, window_start, "session_expire")` at the event's end
  + 60 minutes (`expire_after`, data).

Re-derived on every evaluation like the nudges (daily at 06:00 UTC, on startup, after idle,
after deletion, and after a successful "Add to calendar", which already calls
`guardian.reconcile_user`). `replace_existing=True`, so a moved event moves its jobs. Nothing
is persisted; #223's "exclusivity, not durability" stands.

**The rule changes.** An anchor event satisfies the rule only while it is still ahead **or** a
committed session exists for the day it planned. Past and uncommitted falls through to the
existing stored/fallback checks and, finding nothing, to the nudge ladder. The session store
is consulted the way `_resolve_planning_from_stored_sessions` already does.

**Guard at fire time, one source.** `session_start` runs `start_planning_session(user_id)`,
which asks `standing_for` (the same single indexed query the reminder uses) and does nothing
when an `open` session exists or a `committed` one exists for the target day. `cancelled` does
not block. A store failure is logged, metered (`component="session_start",
error_type="guard_failure"`) and treated as "do not start".

**Which day.** `planning_day_for(event_start: datetime) -> date`: `event_start` in the event's
own timezone; hour < 14 → that date, else the next. Logged with the reason.

## Section 2 — The auto-open

**One builder.** `_begin_timeboxing_session_surface` leaves its closure in `route_slack_event`
and becomes `open_session_surface(client, focus, runtime, *, user_id, target_channel) ->
SessionSurface(root_ts, session_key)` in `slack_bot/session_surface.py`. The request path
calls it too; there is one way to build a session surface.

**One kernel turn, host-built.** The job posts the "thinking" message under the root (the
button path's pattern), then runs `_run_adaptive_timebox_turn` with
`action=TimeboxActionEnvelope(session_key, expected_revision=0,
intent=ConfirmPlanningDay(PlanningDay.lock_default(value=<day>, timezone=<event tz>,
lock_revision=1)))` — the envelope the date-card button mints. The kernel has no
`StartSession` gate (`turn()` checks owner, replay, stale revision, committed-cancel and
nothing else), so this is a valid first turn. It locks the day and renders the capture card
with constraints listed and the open question asked.

**The DM.** One message, Admonisher persona: *"Your planning session for {day} is open —
{permalink}"*. Its `ts` is the ladder's `message_id`.

**Failure.** Surface or turn failure: root relabelled `canceled`,
`record_error(component="session_start", error_type="open_failure")`, nothing armed. The
event still exists, so the reconciler's ordinary rule is unaffected.

## Section 3 — The ladder

**`FollowUpSpec.offsets`.** `haunt/messages.py` gains `offsets: tuple[timedelta, ...] | None`;
when present, `HauntingService` schedules attempt *n* at `created_at + offsets[n]` and stops
after the last one (`max_attempts` is implied). `after` + doubling stay for every other caller.

**Arming**, right after the DM: `schedule_followup(message_id=<dm ts>, topic_id=<session_key>,
user_id, channel_id=<dm>, content=<line>, spec=FollowUpSpec(should_schedule=True,
offsets=(2m, 5m, 10m, 20m, 40m), escalation="gentle", cancel_on_user_reply=True))`. Called on
the service directly — the DM is not a runtime-intercepted message.

**Wording is data**, five lines in `haunt/session_start.py`, each ending in the permalink:

1. Your planning session is open.
2. Still waiting — the day isn't planned yet.
3. {start} has passed. Ten minutes in.
4. Twenty minutes. Plan the day or tell me when you will.
5. Last call: the session closes at the end of the hour.

`HauntingAgent._format_followup` selects by attempt when the record carries lines; its
gentle/firm/menacing prefixes stay for callers that do not.

**Cancelling — new wiring.** Every reply or press that reaches the session calls
`haunting_service.record_user_activity(topic_id=session_key)`: the timeboxing resolver in
`route_slack_event` (it has the key), `_on_timebox_artifact_action`, and the date-card
handlers. Commit and cancel call `cancel_followups(topic_id=session_key)`. Without this the
ladder nudges mid-session.

**Expire.** `session_expire` runs `expire_planning_session(user_id)`: if no committed session
exists for the target day, it cancels remaining follow-ups, marks the auto-opened session
`cancelled` (so the guard cannot mistake a dead session for an open one), relabels the root
*"missed"*, posts *"Missed today's planning session."* to thread and DM, and calls
`guardian.reconcile_user(first_nudge_offset=0)` — which, under Section 1's changed rule, posts
a fresh planning card at once. If a committed session exists, it does nothing.

**Two ladders, never at once.** While the session ladder runs the anchor is still "ahead or
current" and the reconciler stays quiet; after expire the reconciler's ladder owns the user.
A late commit stops the reconciler at its next evaluation; a stale card is today's behaviour.

**Single owner.** `demo.py start` enforces one bot (#245); #223 stays open for the general
case.

## Section 4 — Tests

Unit, model stubbed (no judgement over user text exists in this increment; an AST guard on
`haunt/session_start.py` keeps it so):

- `evaluate`: anchor ahead → `session_start` at start, `session_expire` at end + 60 min, no
  nudges; anchor past, no committed session → nudges; anchor past, committed → nothing; anchor
  moved → same `JobKey`, new `run_at`; a 15:00 Amsterdam event is an afternoon session in UTC too.
- `planning_day_for`: 09:00 → same day; 18:00 → next day; 13:59/14:00 boundary; event tz.
- Guard: `open` → no-op; `committed` for the day → no-op; `cancelled` → proceeds; store failure
  → metered no-op.
- Auto-open: request path and job share `open_session_surface`; the job's turn is one
  `ConfirmPlanningDay` at `expected_revision=0`; DM link posted; ladder armed with
  `topic_id=session_key` and the five offsets; turn failure → relabel, nothing armed, metered.
- `FollowUpSpec.offsets`: fires at the listed times from arming (fake clock), five attempts,
  then stops; existing `after`/doubling tests unchanged.
- Cancel: a thread reply through the seam and a button press both cancel; commit and cancel
  cancel.
- Expire: cancels, marks `cancelled`, posts the line, calls `reconcile_user(first_nudge_offset=0)`;
  does nothing when a committed session exists.

E2E in Slack, after the PR is open, from the parent checkout at the branch via stock
`demo.py start`: a card added for now + 3 min → at start the root and capture card appear in
`#plan-sessions` and the DM link arrives; +2 min first nudge; reply in the thread → nothing at
+5/+10. Second run without replying: nudges at +2/+5/+10; after the slot + `expire_after` the
closing line and a fresh planning card. `expire_after` is a setting so the second run can be
shortened for the test.

## Section 5 — Delivery

Branch off `main`; rebase on `origin/main`; push; PR; e2e from the parent checkout at the
branch with stock scripts; transcript and `demo.py status` in the PR; `## Before merging`
checklist (repo AGENTS.md, "Worktrees, e2e testing, and PRs").

Files: `haunt/reconcile.py` (rule + two job kinds), `haunt/session_start.py` (new: policy as
data, `start_planning_session`, `expire_planning_session`, `planning_day_for`),
`haunt/messages.py` + `haunt/service.py` (`offsets`), `slack_bot/session_surface.py` (new,
extracted), `slack_bot/handlers.py` (extraction; activity/cancel wiring),
`slack_bot/planning.py` (guard reuses `_timeboxing_standing`).

Rollback: no schema change; `demo.py start` on the previous `main` sha.

## Out of scope

The slot a fresh card proposes after a miss (#271); the event recording its planned day
(#282); the harness port (#164, spike issue filed with the two facts above); single-owner
scheduling (#223); the reconciler's remaining double-card behaviour beyond the shared guard
(#256).
