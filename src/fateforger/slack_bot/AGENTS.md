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
- Tasks guided refinement entrypoint is `/task-refine`; the command should dispatch a deterministic session start message to `tasks_agent`.
- After `/task-refine`, follow-up thread messages must continue on `tasks_agent` via focus routing instead of re-triage.

## Routing Timeout Diagnostics

- Operator path rule for live audits:
  - when sending test/user messages into Slack threads, use Slack MCP (`agent-slack`) as first choice.
  - use `scripts/dev/slack_user_timeboxing_driver.py` only as deterministic fallback when MCP access/auth is unavailable.
- Before audit replays, restart the local bot process (`scripts/dev/slack_bot_dev.py`) and verify required MCP dependencies are up so results reflect current code.
- `slack_route_dispatch_timeout` in `handlers.py` is a **delivery guard**, not proof that stage logic failed.
- If timeout fallback text appears in Slack, correlate in this order:
  - session log: `graph_turn_start` / `graph_turn_end` for same `thread_ts` and stage
  - session log: `graph_turn_slow` for long stage duration context
  - metrics: `fateforger_errors_total{component="slack_routing",error_type="route_timeout"}`
- Classification rule:
  - timeout + later `graph_turn_end` => delivery timeout (response computed but missed dispatch window)
  - timeout + no `graph_turn_end` => stage still failing/hanging
- During audits, preserve thread identity:
  - if a bot-created root thread differs from the seed thread, use the bot-created `thread_ts` as canonical for log queries.

## Action Handlers

- All Slack button/action callbacks must be registered in `handlers.py` as Bolt listeners.
- Action IDs must use the `FF_` or `ff_` prefix for discoverability.
- Use Pydantic models for action payloads; avoid manual dict parsing of `body["actions"]`.
- Modal submissions route through `handlers.py` view submission listeners.

## Proposal Object Contract

- For any Slack card/modal that represents a proposed object (event, task change, constraint change, etc.), treat Slack as a transport layer over a typed domain object.
- NL thread replies and UI actions must converge to the same typed intent envelope and same submit executor; do not maintain separate business-logic paths.
- NL interpretation must be schema-bound (typed AutoGen output or schema-in-prompt JSON contract), not regex/keyword/substring heuristics.
- If a proposal supports user edits, edits must be represented as typed patch operations (or typed update fields) before execution.
- Every proposal flow must log correlation fields (`proposal_id`, `intent_source`, `intent`, `submit_mode`) and have parity tests proving NL and UI execute the same backend path.

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
