# Meaningful progress for long-running timeboxing turns

**Research note, 2026-08-28.** This decides what signal the Slack timeboxing
agent should expose while it is reading, generating, validating, retrying, and
applying a patch. It does not design a new Slack surface: the only presentation
target is one bounded, updateable Slack card. It also does not solve job
ownership, cancellation, or the route-timeout bug; those remain part of the
active runtime-fix work.

## Answer

The timeboxing harness is already ReAct-like. ReAct is the pattern of
interleaving model reasoning with environment actions, not a missing transport
that must be added to this stack. The harness's own driver is named
`ReactLoopAgent`; one step performs a model call, records tool calls and
results, then continues to another step. The original ReAct paper describes the
same interleaving of reasoning traces and task-specific actions
([Yao et al., 2022](https://arxiv.org/abs/2210.03629)).

The missing piece is a **safe progress projection** from events the loop already
produces into the Slack card. The provider/harness stack supports raw reasoning,
but the two incident sessions emitted no reasoning events at all; even when it
is enabled, it is the wrong user-facing progress API. The smallest correct fit
is therefore:

1. repair the existing hook-to-async bridge so tool start/end events actually
   reach Slack;
2. translate lifecycle and structured tool results into a small typed
   `ProgressEvent` vocabulary;
3. render those events into one rate-limited Slack card; and
4. keep raw reasoning blocks out of that projection.

An SDK `session.event` integration is the better long-term transport, because
it replaces the temporary-file hook with the harness's native typed event
stream. It is not the smallest incident fix because FateForger currently drives
the headless CLI and its bridge records that the packaged SDK runtime was not
yet usable for this profile.

## What exists now

FateForger already has most of the feature, but the last connection is broken:

- [`dsh_progress_hook.py`](../../../src/fateforger/slack_bot/dsh_progress_hook.py)
  is installed on both `PreToolUse` and `PostToolUse`. It maps stable tool names
  to human labels such as “Reading the day”, “Loading your rules”, “Drafting the
  changes”, and “Writing it to the calendar”. The post-tool payload also
  contains the structured tool response.
- [`harness_bridge.py`](../../../src/fateforger/slack_bot/harness_bridge.py)
  launches the CLI, tails the hook file on a polling thread, and forwards each
  event to `on_event`. It permits a turn to run for 600 seconds.
- [`handlers.py`](../../../src/fateforger/slack_bot/handlers.py) supplies an
  `on_phase` callback, but `_note_harness_phase()` calls
  `asyncio.get_running_loop()` from the polling thread. That thread has no event
  loop, so the function catches `RuntimeError` and intentionally drops every
  semantic update. The separate eight-second heartbeat therefore overwrites
  the card with elapsed time only.
- [`progress.py`](../../../src/fateforger/slack_bot/progress.py) already owns a
  bounded, single-message checklist that serializes concurrent updates.
- [`src/tmbx/server.py`](../../../src/tmbx/server.py) returns structured JSON
  facts for preview and commit: `committable`, violations, overspecification,
  refusal reason, conflicts, and transaction id. These are grounded facts that
  can drive useful progress text without interpreting free-form reasoning.

The tests currently preserve the silent-drop behavior: the test for a callback
on a thread without an event loop merely asserts that it does not raise. That
is a resilience property, not proof that the event reached Slack.

## Is the reasoning trace available?

At the capability level, yes. DeepSeek documents streaming
`reasoning_content` separately from visible `content`, including across
tool-calling sub-turns
([DeepSeek thinking-mode documentation](https://api-docs.deepseek.com/guides/thinking_mode/)).
At the harness layer, yes: the LLM contract has a `reasoning-delta` stream
chunk and a durable `reasoning` content block. The event-sourced session log
records raw `assistant/chunk`, `assistant/message`, `tool/call`, `tool/result`,
`step/start`, `step/end`, `turn/start`, and `turn/end` events. See the inspected
DeepSeek Harness source at commit `2308903f9aa328fe0d657653c7f0ce386e6e345b`:

- [`packages/llm/llm/src/types.ts`](../../../../deepseek-harness/packages/llm/llm/src/types.ts)
- [`packages/core/session/src/types.ts`](../../../../deepseek-harness/packages/core/session/src/types.ts)
- [`packages/core/agent-loop/src/agent.ts`](../../../../deepseek-harness/packages/core/agent-loop/src/agent.ts)

For the two failed Slack sessions inspected in this incident, however, the
answer is **no at runtime**. Both used
`deepseek/deepseek-v4-pro-0813:nitro`, but their durable root-session logs
contained zero `reasoning-delta` chunks and zero reasoning content blocks. The
first contained 22 text chunks and 10 tool calls; the second contained 20 text
chunks and 18 tool calls. The request header selected the provider, model, and
token limit but recorded no explicit reasoning setting. Therefore no reasoning
trace was captured for these turns, despite the model and event schema being
capable of carrying one.

At FateForger's **current CLI bridge**, no reasoning stream is delivered to the
caller. Headless stdout contains the final answer; only Pre/Post tool hooks are
forwarded while the turn runs.

Even after moving to the SDK event stream, raw reasoning should not be copied to
Slack. It can contain speculative, private, unstable, or policy-inappropriate
material, and its presence does not make it a faithful description of the
system's state. OpenAI's first-party rationale reaches the same product
boundary: it keeps raw chain of thought hidden from users and surfaces useful
ideas in the answer instead
([OpenAI, “Learning to reason with LLMs”](https://openai.com/index/learning-to-reason-with-llms/)).
For this product, a second summarizer over chain of thought would also add
latency and could turn speculation into an authoritative-looking status.

The defensible distinction is:

- **internal diagnostic trace:** optionally retained under explicit privacy,
  redaction, access, and retention controls;
- **user-visible progress:** a projection of observed lifecycle and tool facts,
  plus tightly constrained model-declared milestones when needed.

## The event contract Slack actually needs

The event source should emit typed facts; the Slack renderer should contain no
planning logic. A minimal envelope is:

```text
ProgressEvent
  run_id, session_key, sequence, occurred_at
  phase: accepted | reading_plan | loading_constraints | drafting_patch |
         validating_patch | revising_patch | awaiting_approval | committing |
         completed | failed | cancelled | superseded
  status: started | succeeded | failed
  evidence: orchestrator | tool_start | tool_result | terminal
  attempt?: integer
  safe_detail?: bounded structured fields
```

`safe_detail` should be allow-listed per event. It may contain counts,
durations, a public day, and refusal codes. It should not contain raw reasoning,
full tool arguments/results, calendar descriptions, secrets, internal paths, or
provider payloads.

Examples of useful Slack copy derived from this contract:

```text
⏳ Reading Friday's calendar
✅ Loaded 14 active scheduling rules
⏳ Drafting the requested changes
⏳ Checking the draft for overlaps
↻ Found 2 conflicts; revising the draft (attempt 2 of 3)
✅ Draft is valid and ready for review
🔒 Waiting for approval before writing to the calendar
⏳ Writing 3 approved changes
✅ Calendar updated — Undo is available
```

These messages describe observed work. They do not claim a percentage: an
open-ended reasoning/tool loop has no reliable denominator, so “70% complete”
would be invented precision.

The renderer should update one Slack Block Kit message, deduplicate unchanged
states, coalesce bursts, and leave terminal state immutable. Slack's official
agent guidance says longer messages updated with `chat.update` should be
updated no more than once every three seconds
([Slack agent messaging guidance](https://docs.slack.dev/ai/developing-agents/));
`chat.update` is explicitly intended for updating ongoing interactive-message
state
([Slack API](https://docs.slack.dev/reference/methods/chat.update/)).

## Viable architectures

### A. Repair and enrich the existing hook bridge — smallest correct fit

Capture the main asyncio loop before entering `asyncio.to_thread()`, then make
the polling-thread callback use `asyncio.run_coroutine_threadsafe()` against
that captured loop. Send typed events rather than display strings through the
bridge. `PreToolUse` opens a step; `PostToolUse` closes it and may project
allow-listed fields from the structured result. The orchestrator emits
accepted/terminal/cancelled/superseded events around the run.

**Optimizes for:** restoring grounded progress quickly without replacing the
working harness transport.

**Sharp tradeoff:** it observes tool boundaries, not the model's token-by-token
thinking. A long model generation before `plan_apply` can still spend time in
one truthful state (“Drafting the requested changes”), but it will no longer
look dead.

This is enough to fix the immediate user experience because the timeboxing
workflow already has semantically strong actions: read, load constraints,
preview/apply, validate/refuse, commit, and undo.

### B. Add a constrained progress tool — optional enrichment

Expose a no-side-effect tool such as `progress_update(milestone, attempt?)`,
where `milestone` is an enum rather than free text. In a long reasoning gap the
agent may call `drafting_patch`, `checking_constraints`, or
`revising_after_validation`. The existing Pre/Post hook then carries it like
any other action.

**Optimizes for:** meaningful updates during model-heavy periods with no
environment action.

**Sharp tradeoff:** it is model-declared, so it is a claim rather than an
observed fact; it consumes an extra tool turn and can be omitted by the model.
It must be optional enrichment, never the liveness mechanism or source of
terminal truth. Free-form “what I am thinking” text would recreate the raw-CoT
problem under a different name.

This is a legitimate ReAct use: progress is an action interleaved with
reasoning. It does not require adopting a separate “ReAct framework”.

### C. Consume the DeepSeek Harness SDK `session.event` stream — best target

Replace the one-shot CLI/file-tail bridge with the harness SDK's JSON-RPC
runtime and subscribe to `session.event`. The SDK protocol already streams full
typed session envelopes, including step/turn lifecycle, tool calls/results,
todo snapshots, assistant chunks, and terminal reasons. Its client supports
notification subscriptions and an indefinite request timeout for legitimately
long turns. See:

- [`packages/sdk/protocol/src/types.ts`](../../../../deepseek-harness/packages/sdk/protocol/src/types.ts)
- [`python/sdk/README.md`](../../../../deepseek-harness/python/sdk/README.md)

**Optimizes for:** one native event stream for progress, observability,
cancellation, descendants, and terminal classification; no polling file or
profile hook dependency.

**Sharp tradeoff:** it is a transport/runtime migration. The stream contains
far more data than Slack may see—including raw reasoning and tool payloads—so a
strict allow-list projection is mandatory. FateForger's bridge also documents a
prior SDK runtime-carrier failure for this profile, which must be revalidated
before choosing this as the incident fix.

## Why not migrate to LangGraph or AutoGen for this?

Both frameworks demonstrate the right abstraction, but neither is needed to
obtain it here:

- LangGraph streams node state updates, task events, tool events, tokens, and
  user-defined custom events; its official docs explicitly support combining
  `updates` and `custom` streams
  ([LangGraph streaming](https://docs.langchain.com/oss/python/langgraph/streaming)).
- AutoGen's `run_stream()` yields typed tool request/execution events and a
  final result, and custom agents can implement `on_messages_stream()`
  ([AutoGen agents](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/agents.html),
  [AutoGen custom agents](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/custom-agents.html)).

Those are useful reference designs: progress is a projection of typed events,
not scraped prose. But migrating the active harness to either framework to gain
streaming would replace an existing ReAct loop and event model with another
one. It is substantially larger than repairing the current adapter, and it
does not by itself solve safe Slack projection, background job ownership, or
timeout cancellation.

## Recommendation

Adopt **A now**, with the typed contract shaped so the producer can later swap
from hook events to SDK events without changing Slack. Consider **B only if
measured runs still contain unacceptable model-only gaps** after A. Move to
**C** as a separate transport-hardening change once the tmbx profile is proven
to initialize and run correctly through the packaged SDK runtime.

The architectural boundary should be:

```text
hook adapter now / SDK adapter later
              -> ProgressEvent projector
              -> bounded event bus
              -> Slack progress card
              -> structured observability backend
```

This gives the user meaningful progress now, preserves the same events for the
queryable observability ticket, and avoids coupling presentation to a provider's
reasoning format.

## Validation required before calling it fixed

1. A regression test proves a hook callback originating on the polling thread
   reaches the Slack updater on the main loop.
2. A deterministic fake run shows ordered start/end updates for read,
   constraint load, patch preview, validation/refusal, retry, approval, commit,
   and terminal outcomes.
3. Raw reasoning, raw tool arguments/results, secrets, and internal paths never
   appear in the rendered Slack payload.
4. Slack updates are coalesced to at most one update per three seconds and a
   stale heartbeat cannot overwrite a newer semantic or terminal state.
5. Cancellation/supersession emits one terminal event and prevents any later
   orphan event from updating the card.
6. A real restarted Slack audit covers success, validation retry, approval,
   commit, timeout, cancellation, and exporter/Slack-update failure.

## Implemented and replayed outcome (2026-08-29)

Issue #40 implemented option A with the constrained option-B enrichment. The
CLI hook now emits typed lifecycle facts; two model-facing progress tools emit
closed semantic codes only; the Slack presenter owns all human-readable copy.
Malformed progress lines are discarded without logging their contents. The
provider still runs at low reasoning effort, but raw reasoning is neither
required nor exposed.

The runtime now owns one cancellable process group per Slack thread. A newer
turn signals supersession, waits for the old child to be reaped, then starts;
its progress card resolves all running rows in one terminal update. The Slack
route may outlive the delivery guard while continuing to edit the existing
card, which removes the false-timeout behavior without leaving orphan workers.

Approval is bound to an immutable candidate rather than a second model run.
The PostToolUse hook privately exports the exact committable snapshot+patch and
tmbx canonical render. Slack stores it under an opaque candidate id, displays
the canonical render, atomically consumes the id once, and submits the stored
payload directly to `plan_commit`. Duplicate delivery and stale buttons fail
closed; an in-flight commit fences subsequent planning for that thread.
The pre-merge review tightened this boundary further: the candidate now belongs
to the initiating Slack user, both direct and redirected timeboxing routes post
the same approval card, and approval runs in a tracked shielded task that owns
the thread before any Slack or calendar I/O. The candidate digest crosses the
MCP boundary as an idempotency key; tmbx validates it against the exact raw
snapshot+patch and uses the existing journal transaction id for durable replay.
This required no schema change. If the server dies in the narrow interval after
the external calendar write but before journaling, the original snapshot is
stale on retry, so tmbx fails closed rather than duplicating the write.

The final synthetic Slack replay used the real DeepSeek V4 Pro route, real
calendar reads, and real constraint memory without committing. The progress
card reported grounded milestones and validation attempts; the accepted
candidate preserved four exact anchors. Its separate approval card was a reply
in the proposal thread and carried only `thread_key` plus a 24-character opaque
`candidate_id`. A live supersession replay terminated the first card once as
superseded, then produced only the replacement one-block candidate and one new
approval card.

The replay also found a model-behavior edge case: “work window 08:45–17:30” was
initially encoded as an occupying block and overlapped every fixture block. The
profile now states that work/availability windows are boundary constraints,
adds a mechanical pre-apply shape check, and tells the model to remove any such
self-invented boundary block and retry. Replaying the same prompt then produced
the clean four-block candidate. Prometheus stayed healthy, reported no errors
or failed tool calls in the post-audit window, and measured Slack route p95 at
about 29 seconds—slow enough to need progress, but below the delivery timeout.
