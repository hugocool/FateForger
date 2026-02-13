# 📋 Ticket: Skeleton Pre-Generation + Slack Confirm/Undo Buttons + E2E Tests

## Tracking

- Status: Implemented, Tested (2026-02-13)
- System of record issue: https://github.com/hugocool/FateForger/issues/7
- Issue branch: `issue/7-skeleton-pre-generation`
- PR: https://github.com/hugocool/FateForger/pull/8
- Working notebook: `notebooks/WIP/7_skeleton_pre_generation.ipynb`

## Goal
Three interconnected features for the timeboxing flow:

1. **AC6 Skeleton Pre-Generation** — Start drafting the skeleton `TBPlan` in background during Stage 2 so Stage 3 is near-instant.
2. **Slack Confirm/Undo Buttons** — Add Block Kit interactive buttons to Stage 5 (ReviewCommit): user must explicitly Confirm before GCal submission; after submission, show Undo button. Session state tracks the full transaction history for deterministic undo.
3. **E2E Slack Integration Tests** — Automated tests covering the button flows + session memory + undo/redo determinism.

## Scope
- Modify `StageReviewCommitNode` — stop auto-submitting; instead return a confirmation prompt with buttons.
- Modify `PresenterNode` — support blocks (Block Kit) alongside text.
- Add new Slack action handlers: `ff_timebox_confirm_submit`, `ff_timebox_undo_submit`.
- Add `_last_transaction` to Session (so undo survives across turns, not just `CalendarSubmitter` instance state).
- Background skeleton pre-generation in Stage 2.
- Unit tests for button payload construction, session state transitions, undo determinism.
- E2E tests simulating the full Slack flow.

## Non-Goals
- Multi-level undo (only last transaction).
- Redo after undo (v2).
- Slack modal confirmations (buttons are sufficient).

## Acceptance Criteria

### AC1: Skeleton Pre-Generation
- During Stage 2 (CaptureInputs), if calendar immovables + constraints are available, kick off skeleton draft in background.
- Session gains `pre_generated_skeleton: Timebox | None` field.
- `StageSkeletonNode` checks `session.pre_generated_skeleton` first; if present, uses it (near-instant Stage 3).
- If pre-generation is not ready, falls back to synchronous draft (existing behavior).
- Unit test: session with pre-generated skeleton skips LLM call.

### AC2: Confirm Before Submit
- `StageReviewCommitNode` no longer auto-submits to GCal.
- Instead, it runs the review gate LLM, sets `session.pending_submit = True`, and returns the review summary.
- `PresenterNode` detects `session.pending_submit` and appends Confirm/Cancel buttons (Block Kit actions block).
- Slack handler `ff_timebox_confirm_submit` triggers actual submission via `CalendarSubmitter.submit_plan()`.
- On success: updates message with ✅ result + Undo button.
- On cancel: clears `pending_submit`, returns to Refine stage.

### AC3: Undo Button
- After successful GCal submission, message includes an Undo button (`ff_timebox_undo_submit`).
- Slack handler calls `CalendarSubmitter.undo_last()`.
- On success: updates message with ↩️ undo result, session returns to Refine stage.
- Session gains `last_sync_transaction: SyncTransaction | None` for undo state persistence across turns.
- Undo button is disabled/removed after undo completes or session ends.

### AC4: Session Memory Determinism
- `Session.last_sync_transaction` stores the `SyncTransaction` after each submit.
- Undo reads from session (not just `CalendarSubmitter` instance).
- After undo, `session.tb_plan` is restored to `session.base_snapshot`.
- `session.event_id_map` is updated after both submit and undo.
- Unit tests verify session state transitions: pending → submitted → undone → refine.

### AC5: E2E Slack Integration Tests
- Test file: `tests/integration/test_slack_timebox_buttons.py`.
- Test: confirm button triggers submission, session state updated.
- Test: undo button reverses submission, session restored.
- Test: cancel button returns to refine stage.
- Test: undo after session ends is rejected.
- Tests mock MCP but use real session + handler wiring.

## Design Notes

### Button Metadata Pattern
Matches existing codebase:
`value = encode_metadata({"thread_ts": session.thread_ts, "channel": session.channel_id})`

### Confirm Button Block
```json
{
    "type": "actions",
    "block_id": "ff_timebox_review_actions",
    "elements": [
        {
            "type": "button",
            "action_id": "ff_timebox_confirm_submit",
            "text": {"type": "plain_text", "text": "✅ Submit to Calendar"},
            "style": "primary",
            "value": "metadata"
        },
        {
            "type": "button",
            "action_id": "ff_timebox_cancel_submit",
            "text": {"type": "plain_text", "text": "↩️ Keep Editing"},
            "value": "metadata"
        }
    ]
}
```

### Undo Button Block
```json
{
    "type": "actions",
    "block_id": "ff_timebox_post_submit_actions",
    "elements": [
        {
            "type": "button",
            "action_id": "ff_timebox_undo_submit",
            "text": {"type": "plain_text", "text": "↩️ Undo"},
            "style": "danger",
            "value": "metadata"
        }
    ]
}
```

### Implementation Phases
1. Session fields + button block builders (pure, testable).
2. Node changes (`ReviewCommit` stops auto-submitting, `Presenter` emits blocks).
3. Slack action handlers (confirm, undo, cancel).
4. Skeleton pre-generation (background task during Stage 2).
5. E2E tests.

## Acceptance Criteria Check

- [x] AC1: Skeleton pre-generation in Stage 2 + Stage 3 consume/fallback.
- [x] AC2: Confirm-before-submit Stage 5 flow with confirm/cancel buttons.
- [x] AC3: Undo button flow with session-backed transaction.
- [x] AC4: Session memory determinism (`pending_submit`, `last_sync_transaction`, undo restore path).
- [x] AC5: Integration tests for Slack button flows.

## Validation Executed

- `poetry run pytest tests/unit/test_timeboxing_skeleton_pre_generation.py tests/unit/test_timeboxing_submit_flow.py tests/unit/test_timeboxing_review_submit_prompt.py tests/integration/test_slack_timebox_buttons.py -q`
- `poetry run pytest tests/unit/test_timeboxing_graphflow_state_machine.py tests/unit/test_phase4_rewiring.py tests/unit/test_slack_timeboxing_routing.py tests/unit/test_timeboxing_commit_skips_initial_extraction.py -q`

## Notes / Remaining Human Verification

- Manual Slack verification still required for **User-confirmed working**:
  - run Stage 5 in Slack, click Confirm, verify calendar submission + Undo button
  - click Undo, verify calendar rollback + return to Refine state
  - click Keep Editing, verify return to Refine without submission
