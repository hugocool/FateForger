---
paths:
  - "src/fateforger/slack_bot/*.py"
  - "src/fateforger/slack_bot/**/*.py"
---

# Slack bot — transport over typed domain objects

File index, interaction model and action registry: `src/fateforger/slack_bot/README.md`.

## Proposal object contract

- Any card or modal representing a proposed object (event, task change, constraint change) treats Slack as a transport layer over a typed domain object.
- NL thread replies and UI actions converge to the same typed intent envelope and the same submit executor; never maintain separate business-logic paths.
- NL interpretation is schema-bound (typed AutoGen output or schema-in-prompt JSON contract), never regex/keyword/substring heuristics.
- Edits to a proposal are typed patch operations (or typed update fields) before execution.
- Every proposal flow logs `proposal_id`, `intent_source`, `intent`, `submit_mode`, and has parity tests proving NL and UI execute the same backend path.
- **A reply that presses nothing routes *with the surface described* (`ThreadReplyOutcome.NO_PRESS`); it never falls through as if the thread had no surface** (contract item 7, incident I14).

## Routing

- Intent classification routes through the receptionist agent via LLM handoff tools. **Never** add regex/keyword-based intent routing in `handlers.py`; use LLM classification or explicit slash commands.
- Thread focus (`focus.py`) routes follow-up messages to the owning agent without re-triage.
- Never block a Slack reply on background work (calendar sync, constraint extraction).
- `slack_route_dispatch_timeout` in `handlers.py` is a **delivery guard**, not proof that stage logic failed — triage it against `observability/AGENTS.md`.

## Action handlers

- All button/action callbacks are registered in `handlers.py` as Bolt listeners; modal submissions route through its view-submission listeners.
- Action IDs use the `FF_` or `ff_` prefix.
- Use Pydantic models for action payloads; no manual dict parsing of `body["actions"]`.

## Testing

- Integration tests in `tests/integration/` and `tests/e2e/`; unit tests in `tests/unit/`. Mock `AsyncApp` and `AsyncWebClient` — never call real Slack APIs.
