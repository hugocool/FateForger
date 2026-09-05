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

7. A reply on a proposal thread has three outcomes, never two
- Not a proposal thread: ordinary routing.
- A proposal thread and the reply pressed a control: the surface executes it, through the
  same executor the button calls, and nothing is routed.
- A proposal thread and the reply pressed nothing: the message is routed, prefixed with the
  surface's own description (title, proposed values, status, controls offered), so whichever
  agent answers cannot answer cold.
- Interpreter failure on a proposal thread is reported in-thread and metered
  (`component="surface_intent"`); it never becomes "pressed nothing".
- Surfaces are resolved from durable state (draft store, session store), never from the
  in-memory focus cache. The 2026-09-03 incident is the shape this clause forbids.
- A thread whose root a surface posted belongs to that surface. User focus — the DM-wide
  memory of who last answered — never outranks that ownership. A new surface registers its
  root in the resolver chain (`handlers.route_slack_event`, the ordered resolvers before
  agent selection) or its threads will be routed by focus. What ships today is the
  planning-card case (#310, #320): a `timeboxing_agent` that no explicit per-thread binding
  chose — whether it arrived by focus or as the channel default — is demoted to
  `receptionist_agent`; an explicit per-thread binding (`/ff-focus`) still wins. The general
  form — focus never applies inside any bot-posted thread — waits on #302 re-keying DM
  session threads, where it cannot yet be verified.
- An agent that owns a workflow exposes `question` in every state its surface allows. Asked
  is not started, and asked is not revised: a question changes nothing in the session it is
  asked of (spec: `docs/superpowers/specs/2026-09-05-asked-not-started-design.md`).

## Current Scan (2026-03-06)

1. Planning event card (`slack_bot/planning.py`) - compliant baseline
- NL interpreter (`SurfaceIntentInterpreter`) returns a typed decision (`InterpretedPlanningTurn`), which `bind()` maps to a press.
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

