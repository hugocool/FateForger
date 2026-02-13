# Ticket: Planning Card Deterministic Action States + Idempotent Calendar Add

## Tracking
- GitHub Issue (authoritative): https://github.com/hugocool/FateForger/issues/9
- Related issue: https://github.com/hugocool/FateForger/issues/7
- Suggested issue branch: `issue/9-planning-action-states`
- Active ticket source: GitHub issue

## Note
This file is a local mirror for development convenience only. GitHub Issue #9 is the system of record for engineering execution state.

## Goal
Make planning-card deterministic actions state-driven and trustworthy:
1. Terminal success cannot be re-submitted from UI.
2. Reported success means event persistence is evidenced.
3. State model is MECE and shared across transition/render logic.

## Scope
- Add centralized deterministic state model for planning add-to-calendar flow.
- Enforce legal state transitions.
- Ensure success state renders non-clickable action path.
- Handle ambiguous backend success evidence as non-success.
- Add/adjust tests and docs.

## Non-goals
- Repo-wide migration of all deterministic actions in one ticket.
- Broad Slack architecture rewrite.

## Acceptance Criteria
1. MECE lifecycle states exist and are documented (minimum semantics: proposed/pending/confirmed/denied/failed).
2. `CONFIRMED` (success-equivalent) card is terminal and does not expose callable add action.
3. `PENDING` is non-reentrant; duplicate submissions are not enqueued.
4. `FAILED` shows reason and supports retry path.
5. Ambiguous mutation outcomes (for example `ok=True` but missing evidence) do not render confirmed success.
6. Tests cover transition logic, rendering, and `ok=True + url=None` regression.
7. `src/fateforger/slack_bot/README.md` is updated to reflect current behavior and status.

## Notebook Decision Gate
- Proposed mode: `code-only-mode`
- Rationale: behavior is localized to existing planning/draft state and Slack block rendering paths; no exploratory notebook surface is required for implementation.

## Validation Plan
- Unit: transition rules and render-by-state behavior.
- Integration: add flow (pending -> terminal success/failure), duplicate click guard, ambiguous success evidence case.
- Manual: verify event appears in expected calendar/day and card terminal behavior in Slack.

## Open Items
- To decide: exact state enum names and whether `DENIED` is used in this specific flow now or reserved.
- To do: implement state model, wire transitions, add tests, update README.
- Blocked by: none.
