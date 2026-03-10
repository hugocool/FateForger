# Timeboxing Session Constraint Override Design

## Goal

Make same-day explicit session facts override conflicting profile/default memory, including conditional defaults such as "go to the gym unless another sport is already planned or explicitly stated for that day."

## Problem

The current Graphiti-backed baseline can canonicalize and filter durable constraints, but it still selects defaults too aggressively when same-day context should suppress them. In practice, a profile/default activity preference can remain active even after the user has stated a conflicting same-day activity or the calendar already anchors that day around another activity.

## Design Summary

Keep the fix inside the timeboxing memory-selection path. Reuse existing `hints.aspect_classification` metadata and teach stage-context reconciliation to evaluate conditional applicability before family-level selection.

This keeps the planner from seeing avoidable noise and avoids introducing any new regex or substring heuristics. The storage model from `#81` remains intact; this is a selection-time precedence and suppression fix.

## Existing Seams

- `src/fateforger/agents/timeboxing/nlu.py`
  - The interpreter prompt already defines:
    - `is_conditional`
    - `conditional_on_absent`
    - `conditional_on_present`
    - `excludes_aspect_ids`
- `src/fateforger/agents/timeboxing/agent.py`
  - `_collect_constraints()` computes:
    - raw active constraints
    - applicable active constraints
    - selected active constraints
  - `_reconcile_constraints_for_stage_context()` groups constraints into relevance families and currently picks a winner without evaluating cross-aspect suppression.
  - `_collect_session_aspect_ids()` already extracts explicit same-session aspect IDs.
- `src/fateforger/agents/timeboxing/constraint_reconciliation.py`
  - Canonicalizes durable rows and applies date/stage applicability.
  - Not the right place for session-only precedence logic.

## Recommended Approach

### 1. Build same-day aspect context

Before family reconciliation, derive a set of active aspect IDs that represent stronger same-day facts. This set should include:

- session-scoped constraint aspect IDs
- aspect IDs from already applicable same-day constraints
- explicit blockers surfaced through `excludes_aspect_ids`

This context is only for the current turn/session and should not mutate stored durable records.

### 2. Evaluate conditional applicability before reconciliation

Add a conditional-applicability filter that checks `hints.aspect_classification`:

- if `conditional_on_present` is non-empty, require one of those aspect IDs to be active
- if `conditional_on_absent` is non-empty, suppress the candidate when any listed aspect ID is active
- if `excludes_aspect_ids` overlaps the active aspect set, suppress the candidate unless the candidate itself is the stronger session-scoped fact

This should happen before selecting one winner from a relevance family.

### 3. Preserve family reconciliation, but only among eligible candidates

Keep `_constraint_relevance_family_key()` and `_constraint_rank_for_stage_reconciliation()`, but apply them only to constraints that remain eligible after conditional suppression.

If an entire family is suppressed, it should disappear from `session.active_constraints`.

### 4. Make suppression observable

Extend the existing `constraints_active_snapshot` debug event with deterministic fields:

- `active_suppressed_count`
- `active_suppressed_reasons`
- optional preview of suppressed names/reasons for the first few candidates

This preserves auditability without changing user-facing Slack copy in this ticket.

## Ownership Boundaries

- `agent.py`
  - owns session-time precedence, suppression, and active-constraint selection
- `constraint_reconciliation.py`
  - continues to own durable-row canonicalization and basic day/stage applicability
- `nlu.py`
  - already defines the metadata contract; only prompt wording changes are needed if tests show coverage gaps

## Non-goals

- deduplicating dual extraction per Refine turn (`#104`)
- Graphiti deployment/runtime DB audit (`#90`)
- broader cross-agent memory redesign (`#64`)
- changing durable storage schema or adding migrations

## Testing Strategy

Add targeted regressions around selection semantics:

1. Session override
- A session-scoped explicit activity beats a conflicting profile/default activity.

2. Conditional suppression
- A profile/default `gym_training` preference is suppressed when another same-day sport is explicit.

3. Non-conflict preservation
- The same gym preference remains eligible when no blocker exists.

4. Observability
- The session debug snapshot exposes suppression counts and reasons deterministically.

## Risks

- Over-suppressing constraints if family/category rules are too broad.
- Hidden coupling between current family keys and the new active-aspect context.

The mitigation is to keep the first pass narrow: drive suppression only from explicit `aspect_classification` metadata already present on candidates, and prove behavior with focused unit tests before broadening the model.
