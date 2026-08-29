# Timebox Progress Event Seam Design

Issue: https://github.com/hugocool/FateForger/issues/40

## Problem

A timeboxing turn can spend several minutes alternating between model work,
MCP calls, validation, and revision. Slack currently communicates elapsed time,
which proves only that a timer is alive. The incident behind issue #40 also
showed that Slack could cancel its route while the DeepSeek child continued in
the background and that progress callbacks from the worker thread never
reached the Slack event loop.

The progress system must expose grounded work without exposing chain-of-thought,
calendar payloads, prompts, tool arguments, secrets, or speculative model prose.

## Selected architecture

All producers converge on one versioned `TimeboxProgressEvent`. A reducer owns
state transitions. A Slack presenter renders that state into one Block Kit card.
The event is also the future persistence boundary for a queryable logging
backend.

```text
DeepSeek Pre/PostToolUse adapter ──┐
bounded root-agent progress MCP ───┼─> TimeboxProgressEvent
runtime terminal events ───────────┘            │
                                                v
                                     TimeboxProgressReducer
                                         │             │
                                         v             v
                                  Slack card sink   event store
```

The implemented slice uses DeepSeek lifecycle hooks for authoritative tool and
validation facts plus a separate bounded MCP server for skeleton understanding
and material scheduling decisions. Reporting is deliberately not part of
`timebox_patch`, and it never mutates the draft. Native MCP progress remains a
future transport option; raw reasoning is never a producer.

A synthetic spike confirmed that DeepSeek can emit the constrained producer
alongside private reasoning. The production prompt should request it only for
model-only intervals that cannot be inferred from neighboring tool events; the
hook adapter remains authoritative for all observable tool stages and results.

## Design lenses

### User experience

One existing processing message is edited in place. The card reports stages,
attempts, and validated outcomes. Updates are coalesced to at most one Slack
edit per three seconds. There is no percentage unless a producer knows a real
total; open-ended model loops do not invent one.

### Domain ownership

`tmbx` owns facts derived inside planning operations, such as block counts and
validation violations. The DeepSeek bridge owns tool lifecycle and model-loop
attempt facts. Slack owns presentation only and must not inspect raw domain
payloads to rediscover meaning.

### Security and privacy

Events have fixed fields. There is no arbitrary `message`, `reasoning`,
`payload`, `prompt`, or `tool_arguments` field. Refusals use stable codes, not
server text. The Slack presenter generates user copy from enums and scalar
counts.

### Operations

Each event carries `session_key`, `source`, `phase`, `status`, and `sequence`.
Those fields are correlation-safe for logs and low-cardinality metrics. Slack
delivery failure cannot fail the timeboxing turn. Unknown event versions fail
closed at the adapter boundary and are logged without their raw body.

### Testability

The event codec, each producer adapter, reducer, and Slack sink are separate
units. Tests can prove redaction and transitions without starting Slack or an
LLM. Contract tests can prove that the Python MCP server emits a valid event;
the DeepSeek Harness repository owns the transport test for
`notifications/progress`.

## APIs and facades

### Transport-neutral event

```python
class ProgressSource(StrEnum):
    HARNESS_HOOK = "harness_hook"
    TMBX_MCP = "tmbx_mcp"
    AGENT = "agent"
    RUNTIME = "runtime"

class ProgressPhase(StrEnum):
    PREPARING = "preparing"
    UNDERSTANDING_SKELETON = "understanding_skeleton"
    READING_PLAN = "reading_plan"
    LOADING_CONSTRAINTS = "loading_constraints"
    WEIGHING_OPTIONS = "weighing_options"
    DRAFTING_PATCH = "drafting_patch"
    VALIDATING_PATCH = "validating_patch"
    REVISING_PATCH = "revising_patch"
    AWAITING_APPROVAL = "awaiting_approval"
    COMMITTING = "committing"
    UNDOING = "undoing"

class ProgressStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"

@dataclass(frozen=True)
class TimeboxProgressEvent:
    session_key: str
    sequence: int
    source: ProgressSource
    phase: ProgressPhase
    status: ProgressStatus
    attempt: int | None = None
    block_count: int | None = None
    violation_count: int | None = None
    violation_kinds: tuple[str, ...] = ()
    overspecified_count: int | None = None
    refusal_code: str | None = None
```

`to_json()` serializes only these fields. `from_json()` requires version 1,
rejects unknown keys, validates non-negative counts, and bounds strings and
collections. `session_key` is supplied by the host and never inferred from a
payload.

### Producer facade

```python
class ProgressSink(Protocol):
    async def emit(self, event: TimeboxProgressEvent) -> None: ...

class ProgressReporter:
    async def started(self, phase: ProgressPhase, **facts) -> None: ...
    async def succeeded(self, phase: ProgressPhase, **facts) -> None: ...
    async def failed(self, phase: ProgressPhase, **facts) -> None: ...
```

The reporter owns monotonically increasing sequence numbers and injects source
and session identity. Producers cannot choose presentation copy.

### Harness hook adapter

```python
def from_hook_envelope(
    envelope: Mapping[str, object], *, session_key: str, sequence: int
) -> TimeboxProgressEvent | None:
    tool = stable_tool_name(envelope)
    if envelope["hook_event_name"] == "PreToolUse":
        return started_event(tool)
    result = parse_structured_result(envelope.get("tool_response"))
    return safe_result_event(tool, result)
```

The adapter reads only stable tool identifiers and allow-listed result fields.
It never forwards raw input or result text.

### MCP-native facade

```python
class McpProgressReporter:
    def __init__(self, context: Context | None): ...

    def emit(
        self,
        event: TimeboxProgressEvent,
        *,
        progress: float,
        total: float | None = None,
    ) -> None:
        if self._context is not None:
            self._context.report_progress(
                progress=progress,
                total=total,
                message=event.to_json(),
            )
```

The MCP message is machine-readable JSON, not user copy. The client adapter
validates it into the same event contract before publishing it to the reducer.

### Reducer and Slack facade

```python
@dataclass(frozen=True)
class ProgressViewState:
    stages: tuple[StageState, ...]
    terminal: bool

class TimeboxProgressReducer:
    def apply(
        self, state: ProgressViewState, event: TimeboxProgressEvent
    ) -> ProgressViewState: ...

class SlackProgressCard:
    async def emit(self, event: TimeboxProgressEvent) -> None:
        self._state = self._reducer.apply(self._state, event)
        await self._scheduler.request_render(self._state)
```

The presenter maps enums to copy. For example, `REVISING_PATCH + FAILED +
violation_count=2 + violation_kinds=("overlap",)` becomes “2 overlaps;
revising.”

## End-to-end pseudocode

```python
async def handle_slack_turn(thread_key, processing_message):
    card = SlackProgressCard(existing_message=processing_message)
    child = OwnedHarnessProcess(thread_key=thread_key)

    try:
        await card.emit(progress.started(PREPARING))
        reply = await child.ask(
            on_hook=lambda raw: publish_from_worker_thread(
                from_hook_envelope(raw, session_key=thread_key)
            )
        )
        await replace_card_with_final_reply(reply)
    except CancelledError:
        await child.terminate_and_reap()
        raise
    finally:
        await card.close()
```

```python
async def plan_apply(snapshot, patch, ctx: Context):
    progress = McpProgressReporter(ctx, session_from_request(ctx))
    progress.emit(started(VALIDATING_PATCH), progress=0, total=3)
    parsed = validate_input(snapshot, patch)
    progress.emit(succeeded(VALIDATING_PATCH), progress=1, total=3)
    preview = service.apply(parsed)
    progress.emit(result_event(preview), progress=3, total=3)
    return serialize(preview)
```

## Failure behavior

- A Slack edit failure is logged and swallowed by the presentation sink.
- An invalid or unknown progress event is rejected without logging its raw
  payload and cannot fail the timeboxing run.
- A superseding turn terminates and reaps the previous child before the new
  child can commit.
- Slack's delivery guard may stop awaiting a route, but the route remains
  explicitly owned and observed; it does not receive a false timeout reply.
- MCP progress is optional. Lack of client support degrades to harness hook
  events, not to failure.

## Acceptance criteria

1. A harness-backed timeboxing turn edits one existing Slack processing message
   with semantic progress and posts no progress-message stream.
2. The first tool lifecycle event crosses from a worker thread to the captured
   Slack loop; subsequent bursts are coalesced to no more than one edit per
   three seconds.
3. Tool results can expose counts and stable refusal categories, but tests prove
   that prompts, reasoning, tool arguments, raw calendar text, and secrets do
   not serialize into `TimeboxProgressEvent`.
4. A route that exceeds Slack's delivery guard remains owned and observed
   without a false timeout message.
5. Cancellation, a newer same-thread turn, and cancellation during Approve all
   terminate and reap the child process.
6. The `tmbx` MCP facade can emit versioned progress through
   `Context.report_progress` without changing a tool's domain result.
7. Native MCP progress transport is tracked separately against the DeepSeek
   Harness client and issue #40 remains functional through hook events until
   that transport exists.
8. Raw reasoning is diagnostic-only and never rendered in Slack.

## Ownership and delivery slices

### FateForger issue #40

Owns the contract, hook adapter, process lifecycle, reducer, Slack card, tmbx
server reporter facade, tests, and documentation.

### DeepSeek Harness follow-up

Owns adding a progress token to `tools/call`, consuming
`notifications/progress`, validating the JSON message, and publishing the
result through a structured session/tool-progress event. It also owns any
constrained agent-authored progress tool because that tool describes harness
work between MCP calls.

### Logging backend issue

Owns durable storage and query APIs for accepted progress events. Issue #40
defines the record to persist but does not add a database schema or dependency.
