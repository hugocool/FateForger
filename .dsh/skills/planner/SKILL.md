---
name: planner
description: Use when Hugo asks about or changes his calendar directly — what is on a day, whether something is already scheduled, creating or moving a single event, or undoing a change that was just made. Inspection and small edits, not building a day (that is timeboxing).
---

# Planner

The calendar as it is, and small deliberate changes to it. Reading is free;
writing is not.

Tools: `mcp__tmbx__plan_read`, `plan_history`, `plan_apply`, `plan_commit`,
`plan_undo`.

## Reading is the common case, and it is not planning

*"Is a timeboxing session on for tomorrow?"* is a question about what exists.
Answer it with `plan_read` and stop. It is not an invitation to plan one, and
offering to build the day when he asked whether something was already there
answers a question he did not ask.

The same applies to *"what does Thursday look like"*, *"when did I move that"*
(`plan_history`), and *"am I free at three"*. Read, answer, stop.

## Changing one thing

The shape is always the same: `plan_read` for the snapshot, `plan_apply` to
patch it, show him what changed, then `plan_commit` once he approves.

`plan_apply` returns `violations`. Read them back before asking for approval —
a structural problem he finds out about at commit time is one you could have
told him about while it was still free to fix.

`plan_commit` is gated behind an Approve button in Slack and will be refused
until he presses it. That is not an error to work around. Show the change, ask,
wait.

## The constraints still apply to one event

A single move is exactly where it is tempting to skip the constraint read, and
exactly where skipping it goes wrong quietly: moving one meeting into the deep
work block breaks a rule that the rest of the day is still honouring.

Call `memory_get_active_constraints` with `day_type` for any day you are about to
change. Your system prompt states what to do when a change cannot be made without
breaking a `must` — that policy governs here too, unchanged.

Reads do not need it. Answering what is on Thursday requires no rules.

## Undo

`plan_undo` takes the transaction id that `plan_commit` returned. It exists
because a committed change is not permanent, and offering it is often better
than debating whether the change was right — especially just after a commit, when
the id is still in the conversation.

Say the id when a commit succeeds. He cannot ask for an undo he has no handle on.

## When the subject moves

| He is asking for | Load |
|---|---|
| a whole day planned or restructured | the `timeboxing` skill |
| holding him to something he already committed to | the `admonisher` skill |
