# Progress (Updated: 2026-01-19)

## Done

- Fixed maybe_handle_time_reply AttributeError
- Fixed tool schema strict mode error
- Fixed invalid Slack blocks (placeholder property)
- Fixed invalid thread_ts handling
- Fixed false positive constraint extraction
- Fixed state machine loop after constraint review
- Removed redundant Go to session button from initial commit prompt
- Added loading state when clicking Confirm
- Fixed PlanningGuardian scheduler pickling issue (use in-memory scheduler)
- Made reconcile_all run synchronously on startup for reliable job scheduling
- Updated planning reminders to post to admonishments channel with admonisher persona

## Doing



## Next

- Test the planning nudge system end-to-end
