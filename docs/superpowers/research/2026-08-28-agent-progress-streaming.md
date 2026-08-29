# Grounded progress for long-running timeboxing runs

Issue: https://github.com/hugocool/FateForger/issues/40

## Incident evidence

The two affected DeepSeek Harness sessions used
`deepseek/deepseek-v4-pro-0813:nitro`, but neither persisted any
`reasoning-delta` chunks or reasoning blocks. They did persist normal text and
tool activity:

- session `ee4e4b56`: 0 reasoning deltas, 0 reasoning blocks, 22 text chunks,
  10 tool calls;
- session `0a0b0011`: 0 reasoning deltas, 0 reasoning blocks, 20 text chunks,
  18 tool calls.

The request metadata did not contain an explicit `reasoningEffort`. A separate
diagnostic replay with an explicit effort is required before concluding that
this model/provider route emits a reasoning stream in the current profile.

## Product decision

Raw chain-of-thought is not a suitable Slack progress surface even when a
provider emits it: it may contain private context, unstable hypotheses, or
misleading partial conclusions. User-visible progress should instead be a
safe projection of observable actions and validated results.

The implemented projection uses typed tool lifecycle events and an allow-list
of fields. Examples include block counts from `plan_read`, draft attempt
numbers, validation violation counts/categories, and commit/undo outcomes.
Free-form request text, tool payload bodies, calendar contents, raw reasoning,
and secrets cannot enter the progress event.

## Delivery options considered

1. Typed hook events into one editable Slack card — selected. It works with
   the current CLI bridge, is grounded in observable actions, and bounds Slack
   update volume.
2. A constrained model-authored progress tool — useful later for stages that
   have no tool boundary, but it adds another instruction-following and safety
   surface.
3. Native SDK/session event streaming — the clean long-term transport, but the
   installed SDK/runtime carrier is not yet a production-ready replacement for
   the working CLI path.

The selected design remains compatible with options 2 and 3: both can emit the
same versioned `ProgressEvent` contract without changing Slack rendering.

## Structured reasoning-checkpoint spike

On 2026-08-28, a synthetic in-memory ReAct loop exercised
`deepseek/deepseek-v4-pro-0813:nitro` with three tools only:
`report_timebox_progress`, `plan_read`, and `plan_apply`. No Slack, calendar, or
constraint-store data was sent. The fake validator refused attempt one with two
overlaps and accepted attempt two.

With `reasoning.effort=minimal`, the run completed in 30.31 seconds and emitted
seven schema-valid progress calls with no unknown fields:

- 1.72s: reviewing inputs started;
- 2.63s: reviewing inputs completed and drafting attempt 1 started;
- 13.76s: evaluating options started after validation refused;
- 23.59s: evaluating options completed and drafting attempt 2 started;
- 29.79s: awaiting approval started.

The same responses carried 3,827 reasoning tokens (15,120 captured reasoning
characters), proving structured calls can coexist with private reasoning. The
reasoning content was not printed or used as progress. An explicit high-effort
run was stopped after more than two minutes without a completed first response;
that is a latency finding, not evidence that the structured schema failed.

The spike used a deliberately verbose protocol to expose behavior. Production
should be leaner: existing tool lifecycle hooks already derive reading,
drafting, validation, revision, and approval states. A model-authored progress
tool is justified only for a long model-only interval such as evaluating
alternatives between a refused patch and the next attempt. In this scenario,
that reduces seven authored calls to approximately one.

## Production replay outcome

The 2026-08-29 Slack replay used the real DeepSeek V4 Pro route at low effort,
the real Calendar MCP read path, and the existing constraint store. The root
agent emitted one bounded skeleton-understanding report; the Slack card showed
six preserved anchors and zero remaining items before patching. A material
decision report was correctly absent because the user required the six blocks
to remain exact.

The replay also isolated the patch-convergence defect behind much of the long
run. Set-semantic add operations sharing an insertion anchor are sorted by
handle, while overlap validation walked that plan order. It therefore called
Dinner 19:00-20:00 followed by Deep Work 09:00-10:30 an eleven-hour overlap.
Overlap detection now scans resolved blocks chronologically without changing
their stored/rendered plan order. Replaying the same six-block skeleton then
finished on the second attempt (one malformed handle correction), returned
`committable: true` with zero violations, produced no Slack timeout fallback,
and performed no calendar write.

The final implementation additionally enforces a five-apply per-turn budget,
preserves that counter across plan rereads, requires the typed skeleton report
before `timebox_patch` in Slack-owned turns, and binds approval to the exact
latest committable snapshot+patch digest.
