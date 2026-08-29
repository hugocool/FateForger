# Timebox Progress Event Seam Implementation Plan

**Status (2026-08-29): Implemented and live-validated with approved deviations.**
Tasks 1-4 and the Slack portion of Task 7 shipped. The proposed native MCP
progress transport in Tasks 5-6 was replaced for this slice by a separate,
bounded `progress` MCP server callable by the root agent; actual patch attempts
and validation remain derived from harness hooks. Live testing also added a
five-attempt runtime guard, exact validated-draft commit binding, and a tmbx
overlap-order regression fix. The unchecked boxes below preserve the original
TDD plan rather than pretending the implementation followed it verbatim.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a Slack user informed during long timeboxing runs with grounded, privacy-safe progress events while ensuring every harness child remains owned and cancellable.

**Architecture:** Three producers—DeepSeek lifecycle hooks, native tmbx MCP progress, and a later constrained harness progress producer—converge on one fixed-field `TimeboxProgressEvent`. A pure reducer derives view state; a throttled Slack sink edits one existing Block Kit message. Native MCP transport and model-only progress require a linked DeepSeek Harness change and do not block the hook-backed FateForger slice.

**Tech Stack:** Python 3.11, dataclasses/enums, asyncio/threading/subprocess, FastMCP `Context.report_progress`, Slack Bolt/Block Kit, pytest.

## Global Constraints

- GitHub issue #40 is authoritative; branch is `issue/40-timeboxing-managed-progress` and the working mode is `code-only-mode`.
- Do not add dependencies or change database schemas.
- Do not expose raw reasoning, prompts, tool arguments, calendar payloads, secrets, or free-form model progress in Slack events.
- Slack receives one editable Block Kit card and at most one progress edit every three seconds.
- A progress delivery failure cannot fail the timeboxing run.
- Cancellation and same-thread supersession must terminate and reap the owned child before returning.
- Native MCP progress is optional until the DeepSeek Harness client transports it; hook events remain the working baseline.
- Before every commit/push, present changed files, validation commands, and the proposed commit message to the user.

---

### Task 1: Extract the fixed-field progress contract

**Files:**
- Create: `src/fateforger/slack_bot/progress_events.py`
- Modify: `src/fateforger/slack_bot/dsh_progress_hook.py`
- Create: `tests/unit/test_timebox_progress_events.py`
- Modify: `tests/unit/test_dsh_progress_hook.py`

**Interfaces:**
- Produces: `ProgressSource`, `ProgressPhase`, `ProgressStatus`, `TimeboxProgressEvent.to_json()`, and `TimeboxProgressEvent.from_json()`.
- Consumes: no Slack, MCP, or harness dependency.
- Serialization permits only `version`, `session_key`, `sequence`, `source`, `phase`, `status`, `attempt`, `block_count`, `violation_count`, `violation_kinds`, `overspecified_count`, and `refusal_code`.

- [ ] **Step 1: Write failing codec and redaction tests**

```python
def test_event_round_trip_has_only_fixed_fields():
    event = TimeboxProgressEvent(
        session_key="C1:1.0",
        sequence=3,
        source=ProgressSource.HARNESS_HOOK,
        phase=ProgressPhase.REVISING_PATCH,
        status=ProgressStatus.FAILED,
        violation_count=2,
        violation_kinds=("overlap",),
    )
    payload = json.loads(event.to_json())
    assert payload["violation_count"] == 2
    assert "message" not in payload
    assert TimeboxProgressEvent.from_json(event.to_json()) == event


def test_codec_rejects_unknown_payload_fields():
    raw = valid_event_payload() | {"reasoning": "private chain of thought"}
    with pytest.raises(ValueError, match="unknown progress fields"):
        TimeboxProgressEvent.from_json(json.dumps(raw))
```

- [ ] **Step 2: Run tests and observe the missing module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_timebox_progress_events.py`

Expected: FAIL because `progress_events` does not exist.

- [ ] **Step 3: Implement the fixed-field dataclass and strict codec**

```python
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

    def to_json(self) -> str:
        return json.dumps(self._payload(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> TimeboxProgressEvent:
        payload = json.loads(raw)
        reject_unknown_keys(payload)
        validate_bounds(payload)
        return cls.from_payload(payload)
```

- [ ] **Step 4: Move hook projection imports to the neutral module**

`dsh_progress_hook.py` retains `from_hook_envelope(...)`; it imports the event types instead of defining them. Preserve compatibility aliases `ProgressEvent = TimeboxProgressEvent` while the bridge and tests migrate.

- [ ] **Step 5: Run contract and hook tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_timebox_progress_events.py tests/unit/test_dsh_progress_hook.py`

Expected: PASS.

- [ ] **Step 6: Present checkpoint and prepare commit**

Proposed commit: `refactor(progress): extract typed timebox event contract`

---

### Task 2: Make hook projection a source adapter

**Files:**
- Modify: `src/fateforger/slack_bot/dsh_progress_hook.py`
- Modify: `src/fateforger/slack_bot/harness_bridge.py`
- Modify: `tests/unit/test_dsh_progress_hook.py`

**Interfaces:**
- Consumes: `TimeboxProgressEvent` from Task 1 and raw DeepSeek hook envelopes.
- Produces: `from_hook_envelope(envelope, *, session_key, sequence) -> TimeboxProgressEvent | None`.
- `harness_bridge._tail_progress` forwards typed events and temporarily accepts pre-v1 label lines during rolling restarts.

- [ ] **Step 1: Add failing source/identity tests**

```python
def test_hook_adapter_injects_host_identity_and_source():
    event = from_hook_envelope(
        plan_read_post(blocks=three_blocks()),
        session_key="C1:1.0",
        sequence=4,
    )
    assert event.source is ProgressSource.HARNESS_HOOK
    assert event.session_key == "C1:1.0"
    assert event.sequence == 4
    assert event.block_count == 3
```

- [ ] **Step 2: Run the focused test and observe the signature failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_dsh_progress_hook.py -k host_identity`

Expected: FAIL because the adapter does not accept identity or sequence.

- [ ] **Step 3: Implement host-injected identity and monotonic sequencing**

```python
sequence = itertools.count(1)

def project(raw: Mapping[str, object]) -> TimeboxProgressEvent | None:
    return from_hook_envelope(
        raw,
        session_key=session_id or "unscoped",
        sequence=next(sequence),
    )
```

- [ ] **Step 4: Prove sensitive result prose cannot cross the adapter**

Add a test whose hook response contains a secret, rendered calendar text, and a free-form message. Assert none occur in `event.to_json()`.

- [ ] **Step 5: Run hook and real-child cancellation tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_dsh_progress_hook.py`

Expected: PASS.

- [ ] **Step 6: Present checkpoint and prepare commit**

Proposed commit: `feat(progress): adapt harness lifecycle events safely`

---

### Task 3: Separate reducer state from Slack delivery

**Files:**
- Modify: `src/fateforger/slack_bot/progress.py`
- Modify: `src/fateforger/slack_bot/handlers.py`
- Modify: `tests/unit/test_slack_progress.py`
- Modify: `tests/unit/test_schedular_routes_to_harness.py`

**Interfaces:**
- Consumes: `TimeboxProgressEvent`.
- Produces: pure `TimeboxProgressReducer.apply(state, event) -> ProgressViewState` and async `SlackProgressCard.emit(event) -> None`.
- `SlackProgressCard.close()` force-flushes once and ignores later events.

- [ ] **Step 1: Add failing reducer transition tests**

```python
def test_failed_validation_creates_a_revision_then_attempt_two():
    state = reduce_events(
        started(DRAFTING_PATCH, attempt=1),
        failed(REVISING_PATCH, violation_count=2, violation_kinds=("overlap",)),
        started(DRAFTING_PATCH, attempt=2),
    )
    assert state.rows[-2].detail == "2 overlaps; revising"
    assert state.rows[-1].label == "Drafting changes — attempt 2"
```

- [ ] **Step 2: Run reducer test and observe the missing reducer failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_slack_progress.py -k revision_then_attempt_two`

Expected: FAIL because rendering state is embedded in `HarnessProgressCard`.

- [ ] **Step 3: Extract reducer and keep the Slack facade small**

```python
class SlackProgressCard:
    async def emit(self, event: TimeboxProgressEvent) -> None:
        self._state = self._reducer.apply(self._state, event)
        await self._channel.replace(self._presenter.render(self._state))
```

- [ ] **Step 4: Preserve worker-thread delivery through the captured loop**

`handlers._note_harness_phase` must use
`asyncio.run_coroutine_threadsafe(card.emit(event), slack_loop)`. Do not call
`asyncio.get_running_loop()` in the poller thread.

- [ ] **Step 5: Verify one-message delivery, three-second coalescing, and close behavior**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_slack_progress.py tests/unit/test_schedular_routes_to_harness.py`

Expected: PASS with no `chat_postMessage` call from the progress card when an existing message timestamp is supplied.

- [ ] **Step 6: Present checkpoint and prepare commit**

Proposed commit: `feat(slack): render one reduced timebox progress card`

---

### Task 4: Finish route and process ownership

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py`
- Modify: `src/fateforger/slack_bot/harness_bridge.py`
- Modify: `tests/unit/test_slack_message_preroute_guard.py`
- Modify: `tests/unit/test_schedular_routes_to_harness.py`
- Modify: `tests/unit/test_dsh_progress_hook.py`

**Interfaces:**
- Produces: `_owned_harness_ask(...)` as the only Slack path that launches a planning child.
- `harness_bridge.ask(..., cancel_event: threading.Event | None)` terminates and reaps a cancellable child.

- [ ] **Step 1: Keep the existing failing-then-passing regression cases explicit**

Cover route guard timeout, same-thread supersession, external cancellation, and cancellation of the Approve/commit handler.

- [ ] **Step 2: Verify a guarded route is shielded and observed**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_slack_message_preroute_guard.py`

Expected: PASS; the route completes after the guard and no timeout fallback is posted.

- [ ] **Step 3: Verify real subprocess termination**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_dsh_progress_hook.py -k real_child`

Expected: PASS in under two seconds.

- [ ] **Step 4: Verify ordinary and Approve turns share the owner facade**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_schedular_routes_to_harness.py -k 'supersedes or approve_commit'`

Expected: PASS.

- [ ] **Step 5: Present checkpoint and prepare commit**

Proposed commit: `fix(slack): own and cancel long harness turns`

---

### Task 5: Add the tmbx MCP-native progress facade

**Files:**
- Create: `src/tmbx/progress.py`
- Modify: `src/tmbx/server.py`
- Create: `tests/unit/tmbx/test_progress.py`
- Modify: `tests/unit/tmbx/test_server.py`

**Interfaces:**
- Consumes: FastMCP `Context.report_progress(progress, total, message)`.
- Produces: `McpProgressReporter.emit(event, *, progress, total=None) -> None`.
- Tool domain result strings remain byte-for-byte compatible.

- [ ] **Step 1: Add a failing reporter contract test**

```python
def test_mcp_reporter_sends_machine_event_not_user_copy():
    context = RecordingContext()
    reporter = McpProgressReporter(context)
    reporter.emit(validating_started("C1:1.0", sequence=1), progress=0, total=3)
    assert context.calls[0][0:2] == (0, 3)
    assert TimeboxProgressEvent.from_json(context.calls[0][2]).phase is VALIDATING_PATCH
```

- [ ] **Step 2: Run the reporter test and observe the missing module failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/tmbx/test_progress.py`

Expected: FAIL because `tmbx.progress` does not exist.

- [ ] **Step 3: Implement a fail-soft MCP reporter**

```python
class McpProgressReporter:
    def __init__(self, context: Context | None) -> None:
        self._context = context

    def emit(self, event, *, progress: float, total: float | None = None) -> None:
        if self._context is None:
            return
        try:
            self._context.report_progress(progress, total, event.to_json())
        except Exception as exc:
            logger.warning("progress delivery failed type=%s", type(exc).__name__)
```

- [ ] **Step 4: Inject `Context` into `plan_read`, `plan_apply`, `plan_commit`, and `plan_undo`**

Emit start and domain-result events around meaningful operations. Do not emit arbitrary messages, do not change return JSON, and do not add a sixth model-facing tool.

- [ ] **Step 5: Verify progress events and unchanged domain results**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/tmbx/test_progress.py tests/unit/tmbx/test_server.py`

Expected: PASS; existing snapshots/results remain unchanged.

- [ ] **Step 6: Present checkpoint and prepare commit**

Proposed commit: `feat(tmbx): emit native MCP progress events`

---

### Task 6: Track the DeepSeek Harness transport dependency

**Files:**
- Modify: `docs/superpowers/research/2026-08-28-agent-progress-streaming.md`
- Modify: `src/fateforger/slack_bot/README.md`
- GitHub: create a linked engineering issue describing the upstream client change.

**Interfaces:**
- DeepSeek client request adds `_meta.progressToken` to `tools/call`.
- Client request options consume `notifications/progress` and validate the message with `TimeboxProgressEvent.from_json()` or an equivalent generated schema.
- Client publishes a structured `tool/progress` event; it never prints the progress message into model context.

- [ ] **Step 1: Record local evidence**

Document that Python FastMCP exposes `Context.report_progress`, while
`deepseek-harness/packages/mcp/mcp-client/src/tools.ts::callToolUncached` sends
no progress token and installs no progress callback.

- [ ] **Step 2: Create the linked issue payload**

Acceptance criteria:

1. Every eligible `tools/call` gets a unique opaque progress token.
2. Matching notifications become typed `tool/progress` events associated with the exact `ToolExecution`.
3. Unknown, malformed, or late notifications are ignored with bounded diagnostics.
4. Progress content never enters model context or raw Slack rendering.
5. An integration test covers two simultaneous calls without cross-routing notifications.

- [ ] **Step 3: Post links in issue #40 and documentation**

Expected: FateForger issue #40 identifies native MCP progress as an optional linked transport, not a falsely completed local capability.

- [ ] **Step 4: Present checkpoint**

No DeepSeek Harness source edit occurs from the FateForger worktree.

---

### Task 7: Verification, live audit, and draft PR

**Files:**
- Modify: `src/fateforger/slack_bot/README.md`
- Modify: `docs/superpowers/research/2026-08-28-agent-progress-streaming.md`
- Verify all intended issue files with `git status --porcelain`.

**Interfaces:**
- Produces: test evidence, Slack thread reference, correlated session/log references, issue checkpoint, and draft PR.

- [ ] **Step 1: Run static and focused verification**

```bash
git diff --check
.venv/bin/ruff check <changed Python files except documented pre-existing handler violations>
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/unit/test_timebox_progress_events.py \
  tests/unit/test_dsh_progress_hook.py \
  tests/unit/test_slack_progress.py \
  tests/unit/test_schedular_routes_to_harness.py \
  tests/unit/test_slack_message_preroute_guard.py \
  tests/unit/tmbx/test_progress.py \
  tests/unit/tmbx/test_server.py
```

Expected: all tests pass and `git diff --check` is empty.

- [ ] **Step 2: Read and apply `superpowers:verification-before-completion`**

Do not claim completion before fresh evidence is recorded.

- [ ] **Step 3: Run the Slack audit after explicit external-data authorization**

Restart the bot from the issue worktree, verify MCP health and Prometheus scrape,
send the agreed timeboxing prompt, follow the thread to terminal state, and
correlate the canonical thread key with session and patcher logs.

- [ ] **Step 4: Run the explicit-reasoning diagnostic replay after the same authorization**

Set `reasoningEffort: high` through an isolated settings overlay. Record only
request metadata and counts of reasoning/text/tool events; do not quote raw
reasoning.

- [ ] **Step 5: Present pre-commit summary**

List intended files, all commands/tests, and proposed commit messages. Then
commit and push because the user requested an eventual PR.

- [ ] **Step 6: Open a draft PR and post synchronized checkpoints**

Use `gh-workflow-sync checkpoint --stage progress` for issue #40 and the draft
PR. Include the Slack thread, logs, tests, remaining risks, and explicit Open
Items block.

- [ ] **Step 7: Perform the cleanliness check**

Run: `git status --porcelain`

Expected: only files intended for issue #40 remain; no replay overlays or
scratch artifacts remain.
