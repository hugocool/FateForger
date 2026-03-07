---
title: Proposal Object Interaction Contract
---

# Proposal Object Interaction Contract

This document defines the reusable interaction pattern for Slack-facing agent proposals.

## Goal

When an agent proposes an object (or a list of objects), both:
- Slack UI actions (buttons/selects/modals), and
- natural-language thread replies

must converge to the same typed input contract and the same execution path.

## Contract

1. Agent proposes a typed object payload
- Domain object remains typed (`CalendarEventDraft`, `TBPlan`, `TaskEditRequest`, etc.).
- Slack card/modal is only a presentation/control surface for this object.

2. User response becomes a typed decision envelope
- Source can be `ui_action` or `nl_reply`.
- Decision envelope must include:
  - `intent` (for example: ignore, patch_only, submit, patch_and_submit, cancel)
  - optional typed patch payload (domain patch op model, not free-form text parsing)
  - correlation keys (`proposal_id`, `thread_ts`, `user_id`)

3. Patch application is deterministic
- Apply typed patch ops to the current proposal object.
- No regex/substring/keyword extraction from free-form text for behavior-driving intent.
- Structured fields and state transitions are the source of truth.

4. Submission uses one executor
- Both UI and NL paths call the same submit function for the same proposal type.
- No duplicate submit logic between handlers.

5. Observability fields are mandatory
- Emit `proposal_type`, `proposal_id`, `intent_source` (`ui_action` or `nl_reply`), `intent`, `patch_ops_count`, `submit_mode`.
- Keep these fields on both success and error paths.

6. Parity tests are mandatory
- For each proposal surface, include tests proving:
  - UI action and NL confirmation hit the same submit executor.
  - NL patch + submit behavior matches equivalent UI edits + submit.
  - invalid structured interpretation does not silently fallback to heuristic execution.

## Current Scan (2026-03-06)

1. Planning event card (`slack_bot/planning.py`) - compliant baseline
- NL interpreter returns typed decision (`PlanningThreadReplyDecision`).
- NL and button actions converge to `start_add_to_calendar()` and `_add_to_calendar_async()`.
- Existing tests cover NL/action parity.

2. Timeboxing Stage 5 submit - mostly compliant
- UI confirm and NL submit intent both converge to `_submit_pending_plan()`.
- Stage actions already use typed payload models.
- Remaining work: explicitly expose a proposal envelope for review card state transitions.

3. Timeboxing Stage 0 date-commit prompt - partial
- Button/select flow is typed and deterministic.
- NL parity for date-commit prompt is not modeled as a proposal-object interpreter flow.

4. Task details/edit surfaces - partial/non-compliant
- Modal submit path is typed.
- Some NL edit behavior still relies on deterministic regex parsing patterns.
- Needs migration to typed NL decision + typed patch envelope.

5. Constraint review modal - partial
- Modal/action path is typed for metadata.
- No generalized NL interpreter path that maps to the same typed patch intent envelope.

## Rollout Rule

For any new Slack card/modal proposal surface:
- do not ship unless the parity contract (UI + NL -> same typed executor) is implemented and tested.

