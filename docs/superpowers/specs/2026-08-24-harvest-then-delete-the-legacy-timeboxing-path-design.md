# Harvest, then delete: retiring the legacy timeboxing path

**Status:** approved 2026-08-24. Supersedes nothing; sets the finish line the
"legacy stays until I trust the harness" arrangement never had.

## Why this exists

`src/fateforger/agents/timeboxing/agent.py` is 8,827 lines and, as of
`93cb85a`, is not on the path any Slack message takes. `TimeboxingFlowAgent`
registers under exactly one name, `timeboxing_agent`, and that name routes to
the DeepSeek Harness unless `FF_TIMEBOX_BACKEND=legacy` — which is unset, and
which Hugo has said he will not set.

So the file is dormant rather than dead, and dormant is the expensive state. It
kept #189 open as though eight rule violations needed fixing. It produced the
"same persona, different brain" confusion twice in one day, where a thread
answered from the legacy flow while the repo said otherwise. And it has no
deletion criterion, so "until I trust it" has no way to end.

The decision (Hugo, 2026-08-24) is **harvest, then delete**. This spec says what
harvest means concretely and when deletion is allowed.

## The ledger is the artifact

A parent issue holds one row per capability `agent.py` has and the harness does
not. Each row records what it does, the harness equivalent or its absence, and
a verdict: **port**, **redesign**, or **drop**.

**`agent.py` is deleted when every row is closed.** That sentence is the whole
point of the design — it converts an indefinite grace period into a checklist
with an end.

Rows are derived from the nine legacy-only Slack action ids, which collapse to
four capabilities:

| Capability | Legacy | Harness today | Verdict |
|---|---|---|---|
| Submit / confirm / **undo** | 3 buttons | Approve only | **port** |
| Stage navigation | Proceed / Back / Redo / Cancel | none | **decide: redesign or drop** |
| Entry UI | `/timebox` start, day select | bare command | redesign |
| Constraint review rows | per-constraint controls | none | drop unless asked for |

### Undo goes first, because it is a live gap rather than lost polish

A harness turn can **commit** from Slack — `ff_harness_approve` — and cannot
**undo** from Slack. `plan_undo` exists as a tmbx tool with no control attached
to it.

This project's standing rule is that everything should be reversible and
updateable by the agent, preferably through the UI, with the admonishment as the
deliberate exception because a notification cannot be unsent. A calendar write
that can be made but not reversed from the same surface breaks that rule, and it
breaks it in the direction that matters: the irreversible-feeling half shipped
first.

It is also the row with no design questions outstanding. `plan_undo` takes a
transaction id, `plan_history` supplies it, and the commit already returns one.

### Stage navigation is a decision, not a port

Back and Redo cannot be ported. #181 established that the harness holds no state
across turns — goals return `null` on the next call, `--resume` is refused by
the headless profile, and sessions die with the process. Stage navigation
assumes a state machine that no longer exists underneath it.

So the row reads either **"blocked on ACP"**, which would give durable sessions
and make navigation meaningful again, or **"dropped — he steers by typing"**,
which is what happens today and has not yet been reported as a loss. Recording
which, and why, is more valuable than either answer.

## #189 changes meaning rather than closing

The eight pattern-matching sites it catalogues are all behind
`TIMEBOXING_MEMORY_BACKEND=graphiti` or `FF_TIMEBOX_BACKEND=legacy`. Neither is
set, so none of them executes. Its "fix 1-3 first, they are on the live path"
framing predates two migrations that landed after the survey: `memory_kg`
(`2207030`) and timeboxing-to-harness (`93cb85a`).

Its value is not as a fix list. It is **the record of what must not be rebuilt**
during harvest — bag-of-words retrieval that cannot return zero and degrades to
reverse-chronological, a hand-written stopword list that conflated `Work Window`
with `Deep Work Block Duration`, identity keys derived from user-authored text.
Ported carelessly into the harness path, each becomes a *new* violation on live
code, which is strictly worse than leaving them dormant.

It is re-titled and re-scoped rather than closed, and linked from the ledger as
a review gate: no harvest row may be closed without checking its behaviour
against that list.

## The finding nobody wrote down

`src/memory/` — the constraint path the harness actually reads through the
allow-listed server on `:8010` — contains **no `re`, no `.lower()`, no
whitespace tokenising, and no substring tests against user content.** It was
built under the rule and the rule is holding.

This belongs in the record because the only written evidence about pattern
matching on the constraint path is a survey of the system being retired. A
reader finding #189 and nothing else would reasonably conclude the project is
riddled with violations, when the live path is clean.

## Verification before deletion

Reachability has been shown, not proven. `TimeboxingFlowAgent` registers only as
`timeboxing_agent`, and that routes to the harness — but absence of a route is
weaker evidence than absence of an importer.

Deletion requires all three:

1. No import path reaches `agent.py` outside its own package and its tests.
2. No test depends on it as *production behaviour* rather than as a unit under
   test — a suite that goes green because the code it exercised is gone is the
   same silent-wrong-answer shape this project bans elsewhere.
3. A full run with the module removed, including a real `/timebox` turn through
   Slack.

Deleting the `FF_TIMEBOX_BACKEND=legacy` branch happens in the *same* change as
deleting the file. Leaving a flag that selects a missing module turns a
deliberate fallback into an import error at the worst possible moment.

## What this design does not do

**It does not port the five-stage flow.** The stages exist as prose in
`memory-policy.md` and are already in force on the harness path. Rebuilding them
as machinery is a separate decision, and #181 measured why the prompt version is
weaker than it looks: the runtime bounds round counts, not semantic completion.

**It does not fix the eight sites.** Fixing dormant code scheduled for deletion
is the waste this design exists to avoid.
