---
name: timeboxing
description: Use when Hugo wants a day planned or replanned — timeboxing a day, building a schedule, restructuring one that stopped working. Co-creative and staged, ending in an approval he has to give. Not for inspecting or changing a single event (that is the planner).
---

# Timeboxing

Building a day with Hugo, not for him. He confirms each stage before the next
one starts, and nothing reaches his calendar until he approves it.

The tools are `mcp__tmbx__plan_read`, `plan_apply`, `plan_commit`. The first two
are free to get wrong — `plan_read` returns a snapshot and `plan_apply` patches
that snapshot in memory, so a bad draft costs a redraft and nothing else. Only
`plan_commit` touches the calendar, and it is gated: it will be refused until
Hugo presses Approve in Slack. That refusal is the system working. Show him the
day and ask; do not retry the commit.

## Before anything else

1. `plan_read` the day. You cannot plan around commitments you have not looked at.
2. `memory_get_active_constraints` for that day, **with `day_type`**.

Your system prompt states the constraint policy — how `day_type` is chosen, why
`must` is a boundary rather than a preference, what the two correct moves are
when a request cannot be granted without breaking one, and why the suspended
count has to be reported. That policy governs; this skill does not restate it.

What is specific here: **the constraints are read once, at the start, and they
govern every stage that follows.** A `must` discovered at Skeleton does not get
weighed against the shape the day has already taken — the day gives way. If that
means undoing something Hugo locked two stages ago, say so and ask, rather than
quietly keeping the locked thing and breaking the boundary.

## The five stages

Announce which stage you are in. Ask **one** question at a time — a list of
questions is a form, and a form is easy to ignore.

**1. CollectConstraints.** Fixed events, commutes, arrivals, sleep target,
energy profile, which habits are in scope today. Most of this comes from the
`plan_read` and the stored constraints — ask only about what neither answered.
Confirm before moving on.

**2. CaptureInputs.** Tasks, deep-versus-shallow allocation, the one thing that
makes the day a success, secondary goals. Durations are optional here; do not
extract them by interrogation. Confirm.

**3. Skeleton.** Place the immovables, then the one thing in the best deep-work
slot, then the big rocks. Everything is tentative until he locks it. This is the
first `plan_apply` — draft the patch, show what it did, and read `violations`
back. `plan_apply` reports *structural* problems only. A plan can be structurally
perfect and still break one of his rules; that half is yours.

**4. Refine.** Micro-breaks, buffers, shallow work, habits. Defaults worth
starting from, not defending: recovery after deep work, a digestion buffer after
meals, a shutdown ritual at the end. Spend little breath on habits that are
already ingrained — the fragile and the new are what need the attention.

**5. ReviewCommit.** Summarise the day, say plainly how good it is and what gave
way, and ask for approval. Only after he approves does `plan_commit` run.

Stages are a spine, not a cage. He can go back, redo one, or skip ahead, and a
day that only needs two blocks moved does not need all five. What does not bend
is the order of the reads at the top and the approval at the bottom.

## While you are in a session

If he states a durable rule — something about how his days work in general
rather than about this particular Tuesday — record it with `memory_observe`,
passing the session id. A rule he states while planning is the highest-quality
signal this system gets, and it is lost the moment the thread ends.

Do not observe the plan itself. A schedule is not a preference.

## What must not happen

**Do not commit a plan that breaks a `must` without saying so.** That is the one
outcome that is always wrong: the commit is recorded as accepted, and the
acceptance becomes a training label saying Hugo agreed to a day he never saw.

**Do not present a draft as done.** Until `plan_commit` returns, his calendar is
unchanged. Saying "I've scheduled it" when only `plan_apply` ran is a lie he will
discover tomorrow morning.

## When the subject moves

| He is asking for | Load |
|---|---|
| a single event created, moved, or checked | the `planner` skill |
| holding him to something he already committed to | the `admonisher` skill |
