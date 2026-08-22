# Live progress in Slack — design

**Status:** approved 2026-08-22. Supersedes nothing; new surface.

## The problem, stated from the failure that produced it

A timeboxing session sat for twelve minutes showing `Proceeding to the next stage…`.
It was neither stuck nor slow. It had failed:

```
chat.update -> {'ok': False, 'error': 'msg_too_long'}
  timeboxing_stage_actions.py:202  await self._client.chat_update(**update)
```

The Stage 3 message carried the day overview, 26 constraints, three expanded
constraint bodies and five buttons. Clicking **Proceed** tried to rewrite that
whole payload and Slack refused the edit.

Three symptoms, one cause. Everything the flow says goes through **editing one
message that accumulates**, so:

1. it grows until Slack rejects the edit,
2. it cannot report anything *while* working, only at the end, and
3. when the edit fails there is no channel left to say so — the error went to a
   log file the user cannot see.

Silence and progress were indistinguishable. That is the same
silent-wrong-answer shape the project bans elsewhere, arriving through the UI.

## The fix

Keep updating a message in place — that is the preferred feel, and a thread full
of progress notes is noise. **Split what is updated by whether it grows.**

### A live checklist — appended, bounded

One short message per session, rewritten as steps complete:

```
✅ constraints — 30 active, 18 MUST
✅ draft — 6 blocks
⏳ resolving ops…
```

Bounded by the number of steps, not by the size of the artifacts, so
`chat.update` on it cannot fail the way Stage 3 did. A checklist rather than a
single overwritten line because the question being answered is *"is it stuck?"*,
and seeing how far it got answers that; seeing only the current step does not.

### Artifacts posted once, never re-edited

The plan, the constraint list, the buttons — posted when ready and then left
alone. These are what made the message enormous, and nothing needs them
rewritten; they need to *arrive*.

**This is the actual repair for `msg_too_long`:** not fewer edits, but never
re-editing the part that accumulates.

### Failures land in the checklist

```
❌ stage 3 — msg_too_long while rendering
```

The checklist is guaranteed small, therefore guaranteed editable, so the error
channel cannot be taken down by the thing that failed. This is the invariant
that matters most: **a failed step must be visible in Slack, never only in a
log.**

## Components

### `ProgressChannel`

Owns one Slack message per `thread_key` and the checklist within it.

```python
ProgressChannel(client, thread_key)
  .step(label: str) -> None      # append as in-progress
  .done(label, detail="") -> None
  .fail(label, reason: str) -> None
  .close() -> None
```

The only component that knows about Slack. No planning, no tool choice — the
same discipline `harness_bridge` follows.

### Two producers, one consumer

**`/dsh` — a DSH `PostToolUse` hook.** The harness ships a hook protocol
(`PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`,
`UserPromptSubmit`, `Notification`) in `packages/hooks/hook-protocol`. A small
plugin emits a step per completed tool call.

This replaces every approach tried on 2026-08-22 and is why they should not be
revived: log-tailing the servers' stderr worked only under stdio and died when
the servers moved to `streamable-http`; and an attempt to tail their log files
from inside `for line in proc.stdout` could never have fired, because that loop
blocks and warm runs emit nothing until the answer.

**`/timebox` — the stage events it already publishes.** `PresenterNode` already
emits between stages; those go to `ProgressChannel` instead of accumulating into
one message.

## What this does not do

**No token streaming.** The DSH CLI emits the whole answer in one block; there is
no partial text to forward. Real token streaming needs the Python SDK path,
blocked on `deepseek-harness-runtime-bin` still being a PyPI name-reservation
placeholder.

What is delivered is *"this step is done, here is what it was"*.

## Testing

- **Unit:** `ProgressChannel` against a stubbed Slack client — asserts one
  message is created and subsequently updated, never re-created; that `fail`
  renders; that the payload stays bounded as steps accumulate.
- **Regression, the one that matters:** a stage carrying a large artifact must
  not re-edit it. Assert the artifact message id is written once. This is the
  test whose absence let `msg_too_long` reach a user.
- **Integration:** a `/dsh` turn produces at least one step before the final
  answer — i.e. progress genuinely precedes completion rather than arriving with
  it.

## Open

- Whether `/timebox`'s existing five-stage message can be decomposed without
  disturbing the Back/Redo/Cancel actions bound to it.
- Whether the DSH hook plugin ships in the profile or the repo. Profile keeps
  `src/` free of harness specifics; the repo keeps it versioned with its tests.
