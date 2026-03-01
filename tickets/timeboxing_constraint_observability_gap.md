# Ticket: Timeboxing Constraint Traceability in Session/LLM Logs

## Tracking
- Source session: `1772312093.161759` (`2026-02-28`)
- Related symptom: Stage 4 slow/timeout with high constraint volume and unclear memory provenance.
- GitHub issue: pending (`gh auth` currently invalid in this environment).

## Problem
During live session audits, it was not possible to reliably answer:
- Which durable constraints were selected for each stage.
- Which constraints were extracted from user messages.
- Which extracted constraints were persisted locally vs durable memory.
- Which constraints were included in Stage 4 patch input.

This blocks root-cause analysis when patching fails or drifts from user preferences.

## Acceptance Criteria
1. Session logs emit structured events for:
   - durable selection (`durable_constraints_selected`)
   - active merged constraints (`constraints_active_snapshot`)
   - extraction result (`constraint_extraction_result`)
   - local persistence (`constraint_local_persisted`)
   - durable enqueue/apply/noop/error (`durable_upsert_*`)
   - Stage 4 patch constraint budget (`refine_constraints_selected`)
2. Each event includes enough identifiers to correlate by `session_key`, `stage`, and relevant constraint names/uids.
3. Timebox audit script can answer “selected/extracted/persisted/applied” for one session without manual grep.

## Notes
- This repo now includes the missing runtime events in `TimeboxingFlowAgent`.
- Remaining work is operational:
  - restore GitHub auth
  - open upstream issue
  - add one CLI summary command over session logs for a single audit report.
