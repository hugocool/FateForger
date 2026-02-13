# Slack Bot — Agent Notes

**Scope:** Operational rules for `src/fateforger/slack_bot/`. For file index, interaction model, and action registry, see `README.md` in this folder.

## UX Rules

- Keep user-facing responses fast; never block a Slack reply on background work (calendar sync, Notion writes, constraint extraction).
- When background work is in flight, include a short friendly status note so the user knows they can continue.
- Deliver agent status notes verbatim; do not rewrite or embellish them in the Slack layer.
- One user-facing message per Slack turn (enforced by `PresenterNode` in the timeboxing flow).

## Routing

- Intent classification routes through the receptionist agent via LLM handoff tools.
- **Never** add regex/keyword-based intent routing in `handlers.py`. Use LLM classification or explicit slash commands.
- Thread focus (`focus.py`) is the mechanism for routing follow-up messages to the owning agent without re-triage.
- Suppress planning nudges while a timeboxing session is active (handled by `PlanningReconciler`).

## Action Handlers

- All Slack button/action callbacks must be registered in `handlers.py` as Bolt listeners.
- Action IDs must use the `FF_` or `ff_` prefix for discoverability.
- Use Pydantic models for action payloads; avoid manual dict parsing of `body["actions"]`.
- Modal submissions route through `handlers.py` view submission listeners.

## Sync Engine Integration

- `StageReviewCommitNode` emits a `pending_submit` state; no auto-submit in the node path.
- `PresenterNode` attaches review action blocks (confirm/cancel).
- Slack action handlers in `handlers.py`:
  - `ff_timebox_confirm_submit`
  - `ff_timebox_cancel_submit`
  - `ff_timebox_undo_submit`
- Action bridge lives in `timeboxing_submit.py` and dispatches typed messages to `timeboxing_agent`.
- On confirm: call `CalendarSubmitter.submit_plan()`.
- On undo: call `CalendarSubmitter.undo_transaction()` using session-backed transaction state.

## Testing

- Slack integration tests live in `tests/integration/` and `tests/e2e/`.
- Unit tests for Slack-adjacent logic (planning IDs, focus routing) live in `tests/unit/`.
- Mock the Slack `AsyncApp` and `AsyncWebClient` in tests; do not make real Slack API calls.
