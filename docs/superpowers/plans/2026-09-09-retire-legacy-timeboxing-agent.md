# Retire the Legacy Timeboxing Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete `TimeboxingFlowAgent` and everything only it reaches, without any Slack path ever addressing an agent that no longer exists, and without losing the one behaviour the harness lacks.

**Architecture:** The AutoGen handoff to `timeboxing_agent` is a string the receptionist returns and `handlers.py` reads; it never needs a registered agent. Four `runtime.send_message` sites still address the agent directly, so those are closed first — each becomes a call into the adaptive kernel's session surface — and a test that fails today guards them. Then the consecutive-no-progress cap is ported from the legacy agent into `AdaptiveTimeboxing`, and only then does the class, its registration, the `FF_TIMEBOX_BACKEND` flag, and the orphaned subtree go. The reachability script is the authority on what is orphaned; the plan names the expected set so a surprise is visible.

**Tech Stack:** Python 3.11, pytest (`--import-mode=importlib`, `asyncio_mode=auto`), autogen-core / autogen-agentchat, pydantic v2, Slack Bolt.

**Spec:** `docs/superpowers/specs/2026-09-08-test-suite-composability-design.md`, Project 1. Two deviations from it, found while reading the code for this plan, are recorded in Global Constraints and corrected in the spec in Task 9.

## Global Constraints

- **Worktree:** `/Users/hugoevers/VScode-projects/admonish-1/.worktrees/test-suite-prune`, branch `chore/prune-and-consolidate-tests` (PR #396). Branch the work off it as `chore/retire-legacy-timeboxing-agent`. Never touch the parent checkout.
- **Test command**, from the worktree root (there is no `.venv` in a worktree; pytest's `pythonpath = ["src"]` makes the worktree's `src` win):
  `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests -m "not slow" -q --no-header -p no:cacheprovider`
  Baseline on the branch: **3136 passed, 4 skipped, 1 failed** (the failure is `test_the_deployed_profile_matches_the_repository`, an environmental deployed-vs-repo YAML diff; leave it), ~31s wall-clock.
- **The identifier `"timeboxing_agent"` survives everywhere.** It is the handoff target the receptionist's model emits, the persona/channel key in `handlers.py`, the model-routing key in `llm/factory.py`, the session-key prefix `logging_config` parses. Only the class, its `register(...)` call, and the sends to it go.
- **The `HandoffBase(target="timeboxing_agent", ...)` entries in `runtime.py` stay** — they mint the receptionist's `transfer_to_timeboxing_agent` tool.
- **Deviation 1 — `settings.timeboxing_memory_backend` stays.** The spec says remove it; it is also read by `runtime.py:208,335` (graphiti startup checks) and `tasks/defaults_memory.py:224`. Leave it and its validator alone.
- **Deviation 2 — `ConstraintMemoryClient` stays.** The spec says delete `mcp_clients.py` whole; `ConstraintMemoryClient` is the default `tasks_defaults_memory_backend` (`config.py:226`) with three tests in `tests/unit/tasks/test_task_defaults_memory.py`. Only `McpCalendarClient` and `CalendarDaySnapshot` leave that file.
- **CLAUDE.md rules apply**: no `re`, no keyword lists, no substring tests on user content. String comparison here is only ever against identifiers the system minted (agent type names, action ids, outcome kinds).
- **Commit messages** end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Never `git commit -a`; stage named paths.
- **Every task ends green** on the fast suite (minus the one environmental failure) before its commit.

---

### Task 1: The test that fails today — no Slack path may address `timeboxing_agent`

**Files:**
- Create: `tests/unit/slack/test_timeboxing_is_never_addressed_on_the_runtime.py`

**Interfaces:**
- Consumes: `fateforger.slack_bot.handlers.route_slack_event`, `handlers._run_adaptive_timebox_turn` (monkeypatched), `fateforger.slack_bot.focus.FocusManager`, `tests.doubles.slack.RecordingSlackClient`, `fateforger.core.config.settings`.
- Produces: the invariant every later task keeps green. Task 2 makes the routing cases pass; Task 5 makes `test_runtime_registers_no_timeboxing_agent` pass.

- [ ] **Step 1: Write the failing test**

```python
"""Timeboxing has one destination: the adaptive kernel's session surface.

A handoff to ``timeboxing_agent`` is a string the receptionist returns; Slack
code reads it and opens a session. Nothing should ever hand the AutoGen
runtime an ``AgentId("timeboxing_agent", ...)``: once the class is retired
that raises a bare ``Exception("Recipient not found")`` from
``SingleThreadedAgentRuntime.send_message`` and the user sees a warning that
looks like a transport failure.

The fake runtime here answers the receptionist and raises for anything else,
which is exactly what the real runtime does for an unregistered type.
"""

from __future__ import annotations

import ast
import inspect
import types

import pytest

from autogen_agentchat.messages import HandoffMessage

from fateforger.core import runtime as runtime_module
from fateforger.core.config import settings
from fateforger.slack_bot import handlers
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.timeboxing_cards import timebox_failure_message
from tests.doubles.slack import RecordingSlackClient

RETIRING = {"timeboxing_agent"}


def _registered_agent_types() -> set[str]:
    """Every ``X.register(runtime, "<type>", ...)`` in runtime.py, by AST."""
    tree = ast.parse(inspect.getsource(runtime_module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            names.add(node.args[1].value)
    assert "receptionist_agent" in names, names
    return names


class _RealShapedRuntime:
    """Answers the receptionist with a handoff; raises for a retired type."""

    def __init__(self) -> None:
        self.addressed: list[str] = []
        self._known = _registered_agent_types() - RETIRING

    async def send_message(self, message, recipient):
        self.addressed.append(recipient.type)
        if recipient.type not in self._known:
            raise Exception("Recipient not found")
        return types.SimpleNamespace(
            chat_message=HandoffMessage(
                target="timeboxing_agent", content="handoff", source="receptionist_agent"
            )
        )


@pytest.fixture
def harness_turns(monkeypatch):
    """Record every kernel turn instead of running one; a fake runtime has no kernel."""
    turns: list[dict] = []

    async def _turn(**kwargs):
        turns.append(kwargs)
        return timebox_failure_message()

    monkeypatch.setattr(handlers, "_run_adaptive_timebox_turn", _turn)
    # The conftest pins the suite to legacy until Task 5 deletes the flag.
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")
    return turns


async def _say(**_kw):
    return {"channel": "C_ORIG", "ts": "say"}


async def _route(*, runtime, focus, client, event):
    await handlers.route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
        event=event,
        bot_user_id=None,
        say=_say,
        client=client,
    )


def _focus() -> FocusManager:
    return FocusManager(
        ttl_seconds=3600, allowed_agents=["receptionist_agent", "timeboxing_agent"]
    )


@pytest.mark.parametrize(
    ("configured_channel", "event"),
    [
        ("C_TIMEBOX", {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
        ("C_TIMEBOX", {"channel": "D_DM", "channel_type": "im", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
        ("", {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
        ("C_ORIG", {"channel": "C_ORIG", "user": "U1", "text": "timebox tomorrow", "ts": "1"}),
    ],
    ids=["channel-configured", "from-dm", "no-channel-configured", "already-in-the-channel"],
)
async def test_a_handoff_never_addresses_the_retired_agent(
    monkeypatch, harness_turns, configured_channel, event
):
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", configured_channel, raising=False)
    runtime = _RealShapedRuntime()
    client = RecordingSlackClient(root_ts="tb_root", reply_ts="tb_proc", dm_channel="D_DM")

    await _route(runtime=runtime, focus=_focus(), client=client, event=event)

    assert runtime.addressed == ["receptionist_agent"]
    assert len(harness_turns) == 1, "the handoff must reach the kernel exactly once"
    session_channel = configured_channel or event["channel"]
    assert harness_turns[0]["session_key"].startswith(f"{session_channel}:")


async def test_a_second_dm_turn_continues_the_session_on_the_kernel(monkeypatch, harness_turns):
    """The redirect route: once a session is open, the next DM message takes it."""
    monkeypatch.setattr(settings, "slack_timeboxing_channel_id", "C_TIMEBOX", raising=False)
    runtime = _RealShapedRuntime()
    client = RecordingSlackClient(root_ts="tb_root", reply_ts="tb_proc", dm_channel="D_DM")
    focus = _focus()
    dm = {"channel": "D_DM", "channel_type": "im", "user": "U1"}

    await _route(runtime=runtime, focus=focus, client=client, event={**dm, "text": "timebox tomorrow", "ts": "1"})
    await _route(runtime=runtime, focus=focus, client=client, event={**dm, "text": "move gym later", "ts": "2"})

    assert runtime.addressed == ["receptionist_agent"], "the second turn must not go to the runtime at all"
    assert [t["session_key"] for t in harness_turns] == ["C_TIMEBOX:tb_root", "C_TIMEBOX:tb_root"]


def test_runtime_registers_no_timeboxing_agent():
    """Green once Task 5 lands; until then it names what is being retired."""
    assert not (_registered_agent_types() & RETIRING)
```

- [ ] **Step 2: Run it to verify it fails on the right cases**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/slack/test_timeboxing_is_never_addressed_on_the_runtime.py -q --no-header -p no:cacheprovider`

Expected: `channel-configured` and `from-dm` PASS (the surface path already exists); `no-channel-configured` and `already-in-the-channel` FAIL with `Exception: Recipient not found` surfacing as `runtime.addressed == ["receptionist_agent", "timeboxing_agent"]`; the second-DM-turn test FAILS the same way; `test_runtime_registers_no_timeboxing_agent` FAILS.

If `channel-configured` fails instead, read the failure before touching the test: it means `_begin_timeboxing_session_surface` is not reached from the handoff, which contradicts the spike, and Task 2 needs to know.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/unit/slack/test_timeboxing_is_never_addressed_on_the_runtime.py
git commit -m "test(slack): no path may address timeboxing_agent on the runtime

Fails today on two of four handoff configurations and on the second DM
turn -- the redirect route and the handoff fall-through still send to
the agent directly. Green once the sends are closed (Task 2) and the
registration is gone (Task 5).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Close the two live sends in `route_slack_event`

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` — the redirect route (`~3084-3160`) and the handoff block (`~3320-3495`)
- Modify: `tests/unit/slack/test_schedular_routes_to_harness.py:404-470` (`test_the_handoff_interception_uses_the_redirected_thread`)
- Modify (retarget, these asserted the sends): `tests/unit/timeboxing/test_slack_timeboxing_routing.py` (3 tests), `tests/unit/timeboxing/test_slack_timeboxing_channel_redirect.py` (2), `tests/unit/slack/test_slack_timeboxing_surface.py` (1), `tests/unit/timeboxing/test_slack_channel_default_routing.py` (1)
- Delete: `tests/e2e/test_slack_timeboxing_background_status.py` (asserts a legacy stage-message format)

**Interfaces:**
- Consumes: `handlers._run_adaptive_timebox_turn(*, runtime, client, logger, session_key, actor_user_id, interaction_id, progress_channel, progress_ts, card_channel, card_thread_ts, user_text, focus)`, the nested `_begin_timeboxing_session_surface(*, target_channel, origin_key, existing_root=None)`, `handlers._channel_for_agent(agent_type) -> str | None`.
- Produces: `route_slack_event` reaches `_run_adaptive_timebox_turn` at exactly three sites — the session surface (`session_key=redirect.target_key`), the direct in-thread route (`recipient_key`), and the redirect continuation (`redirect.target_key`). Task 5 relies on no send to `timeboxing_agent` remaining.

- [ ] **Step 1: Site 1 — the redirect route continues a timeboxing session on the kernel**

In `route_slack_event`, find the block beginning `redirect = focus.get_redirect(origin_key)` / `if redirect and agent_type == redirect.agent_type:`. After `processing = await client.chat_postMessage(**processing_payload)`, replace the `msg = _build_agent_message(...)` through the end of the `except Exception as e:` handler with:

```python
        if redirect.agent_type == "timeboxing_agent":
            # A redirected timeboxing thread is an open session: continue it on
            # the kernel, keyed by the redirect's own thread. There is nothing
            # registered under "timeboxing_agent" to send to.
            result = await _run_adaptive_timebox_turn(
                runtime=runtime,
                client=client,
                logger=logger,
                session_key=redirect.target_key,
                actor_user_id=user,
                interaction_id=ts,
                progress_channel=redirect.target_channel,
                progress_ts=processing["ts"],
                card_channel=redirect.target_channel,
                card_thread_ts=redirect.target_thread_ts,
                user_text=cleaned_text,
                focus=focus,
            )
        else:
            msg = _build_agent_message(
                agent_type=redirect.agent_type,
                cleaned_text=cleaned_text,
                user=user,
                channel=redirect.target_channel,
                thread_ts=redirect.target_thread_ts,
                ts=redirect.target_thread_ts,
                force_channel=redirect.target_channel,
                force_thread_root=redirect.target_thread_ts,
                force_reply=True,
            )
            try:
                result = await runtime.send_message(
                    msg, recipient=AgentId(redirect.agent_type, key=redirect.target_key)
                )
            except asyncio.TimeoutError:
                ...  # the existing TimeoutError handler, unchanged
                return
            except Exception as e:
                ...  # the existing Exception handler, unchanged
                return
```

The `payload = _compact_slack_payload(**_slack_payload_from_result(result))` continuation that follows stays as is — `_run_adaptive_timebox_turn` returns a `SlackBlockMessage`, the same shape the surface already feeds it.

- [ ] **Step 2: Site 2 — a timeboxing handoff has one destination**

In the same function, find `if handoff_target:` followed by `focus.set_user_focus(user, handoff_target)` and the `should_redirect` computation. Insert **before** that `if handoff_target:` block:

```python
    if handoff_target == "timeboxing_agent":
        # Every door into timeboxing opens the same session surface. When no
        # channel is configured, or the user is already in it, the session
        # lives where they are. Never the fall-through send below: there is
        # nothing registered under this name to receive it.
        await _begin_timeboxing_session_surface(
            target_channel=_channel_for_agent("timeboxing_agent") or channel,
            origin_key=origin_key,
        )
        return
```

Then, inside the existing `if handoff_target:` block, delete the timeboxing special-cases that are now unreachable:
- in `should_redirect`, drop `or handoff_target == "timeboxing_agent"` so it reads `should_redirect = bool(target_channel and target_channel != channel) and (not is_dm)`;
- inside `if should_redirect: try:`, delete the `if handoff_target == "timeboxing_agent": await _begin_timeboxing_session_surface(...); return` branch;
- in the fall-through `handoff_msg = _build_agent_message(...)`, replace the two `force_thread_root=(...)` / `force_reply=(...)` conditionals with `force_thread_root=None, force_reply=None`.

- [ ] **Step 3: Update the AST contract test for three call sites**

In `tests/unit/slack/test_schedular_routes_to_harness.py`, `test_the_handoff_interception_uses_the_redirected_thread`, replace the two assertions at the end (`assert len(direct_calls) == 1, direct_calls` and `assert _session_key_source(direct_calls[0]) == "recipient_key"`) with:

```python
    outside = sorted(_session_key_source(call) for call in direct_calls)
    assert outside == ["recipient_key", "redirect.target_key"], outside
```

and extend the docstring's last paragraph with one sentence: *"The redirect route is the third site: a redirected thread is an open session, so it continues on the kernel keyed by ``redirect.target_key``, never by a send."*

- [ ] **Step 4: Run Task 1's test and the AST test**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/slack/test_timeboxing_is_never_addressed_on_the_runtime.py tests/unit/slack/test_schedular_routes_to_harness.py -q --no-header -p no:cacheprovider`

Expected: everything in the first file passes except `test_runtime_registers_no_timeboxing_agent`; the AST test passes.

- [ ] **Step 5: Retarget the seven tests that asserted the sends**

Each of these drove the legacy dispatch and asserted a `StartTimeboxing`/`TimeboxingUserReply` message reached `timeboxing_agent`. They now assert the session surface. Until Task 5 deletes the flag, each needs `monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")` as its first line (add `monkeypatch` to the signature where missing); Task 5 strips those lines.

`tests/unit/timeboxing/test_slack_timeboxing_routing.py`:
- `test_routes_root_message_to_timeboxing_start_when_focused` → rename `test_a_root_message_in_a_focused_channel_opens_a_session`; replace the assertions with
  ```python
  assert runtime.calls == []
  assert any(p.get("channel") == "C1" and not p.get("thread_ts") for p in client.posted)
  ```
- `test_handoff_from_receptionist_resends_as_timeboxing_start` → rename `test_a_receptionist_handoff_opens_a_session_where_the_user_is`; replace the assertions after the route with
  ```python
  assert [r.type for _, r in runtime.calls] == ["receptionist_agent"]
  assert any(p.get("channel") == "C1" and not p.get("thread_ts") for p in client.posted)
  ```
  and drop the second `_FakeResult(...)` from the runtime's result list (only the receptionist answers now).
- `test_routes_thread_reply_to_timeboxing_user_reply` → rename `test_a_thread_reply_in_a_focused_thread_is_a_kernel_turn`; replace the assertions with
  ```python
  assert runtime.calls == []
  assert client.updates, "the turn's outcome is written back into the thread"
  ```
- Remove the now-unused import `from fateforger.agents.timeboxing.messages import StartTimeboxing, TimeboxingUserReply`.

`tests/unit/timeboxing/test_slack_timeboxing_channel_redirect.py`:
- `test_timeboxing_handoff_redirects_into_configured_channel`: replace the two final assertions with
  ```python
  assert [r.type for _, r in runtime.calls] == ["receptionist_agent"]
  assert focus.get_redirect("C_ORIG:1").target_key == "C_TIMEBOX:tb_root"
  assert any(p.get("channel") == "C_TIMEBOX" and not p.get("thread_ts") for p in client.posted)
  ```
- `test_timeboxing_reply_in_origin_thread_is_forwarded` → rename `test_a_reply_in_the_origin_thread_continues_the_session_on_the_kernel`; replace the two final assertions with
  ```python
  assert [r.type for _, r in runtime.calls] == ["receptionist_agent"]
  assert any(u.get("channel") == "C_TIMEBOX" for u in client.updates)
  ```

`tests/unit/slack/test_slack_timeboxing_surface.py`, `test_timeboxing_handoff_does_not_redirect_from_dm`: replace the first two assertions with `assert [r.type for _, r in runtime.calls] == ["receptionist_agent"]`; keep the third.

`tests/unit/timeboxing/test_slack_channel_default_routing.py`, `test_specialist_channel_routes_directly_to_timeboxing_agent` → rename `test_the_specialist_channel_opens_a_session_directly`; replace the assertions with
```python
assert runtime.calls == []
assert any(p.get("channel") == "C_PLAN" and not p.get("thread_ts") for p in client.posted)
```
and remove the `StartTimeboxing` import.

`git rm tests/e2e/test_slack_timeboxing_background_status.py` — it asserts the legacy "Background:" stage-message text, which has no harness equivalent by design.

- [ ] **Step 6: Run the full fast suite**

Run the Global Constraints test command. Expected: **3139 passed, 2 failed** — the environmental one and `test_runtime_registers_no_timeboxing_agent` (3136 baseline, minus the 1 deleted e2e test, plus Task 1's 6, of which 1 waits for Task 5). If any test outside the files named above fails, it is asserting a send this task closed: read it, and either retarget it the same way or stop and report.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/slack/test_schedular_routes_to_harness.py tests/unit/timeboxing/test_slack_timeboxing_routing.py tests/unit/timeboxing/test_slack_timeboxing_channel_redirect.py tests/unit/slack/test_slack_timeboxing_surface.py tests/unit/timeboxing/test_slack_channel_default_routing.py
git rm -q tests/e2e/test_slack_timeboxing_background_status.py
git commit -m "fix(slack): a timeboxing handoff never reaches the AutoGen runtime

Two sends still addressed timeboxing_agent directly: the redirect route,
which every second DM turn takes once a session is open, and the handoff
fall-through, reached whenever no session channel is configured or the
user is already in it. Both now continue on the kernel. A retired
registration would have turned each into Exception('Recipient not
found') rendered as a warning.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Retire the legacy Slack cards

The confirm/cancel/undo and stage proceed/back/redo/cancel buttons exist only on cards the legacy agent posted. Their dispatchers send to `timeboxing_agent` inside a broad `except Exception` that rewrites the card to "please try again" — a missing agent would read as a transient glitch forever. One handler answers all seven honestly.

**Files:**
- Create: `src/fateforger/slack_bot/retired_cards.py`
- Modify: `src/fateforger/slack_bot/timeboxing_commit.py` — move `encode_metadata`/`decode_metadata` in; delete `TimeboxingCommitCoordinator` and `_slack_payload_from_result`
- Modify: `src/fateforger/slack_bot/handlers.py` — imports at `~137-170`, coordinator construction at `~3567-3571`, the ten `@app.action(FF_TIMEBOX_*)` handlers at `~4625-4700`, and the two `if _timebox_backend() == "legacy": await timeboxing_commit.handle_*` blocks at `~4488` and `~4608`
- Delete: `src/fateforger/slack_bot/timeboxing_submit.py`, `src/fateforger/slack_bot/timeboxing_stage_actions.py`
- Create: `tests/unit/slack/test_retired_cards.py`
- Modify: `tests/unit/slack/test_slack_boundary_withholds_tool_results.py:158-163`
- Delete: `tests/unit/timeboxing/test_timeboxing_submit_flow.py`, `tests/unit/timeboxing/test_timeboxing_stage_actions.py`, `tests/unit/constraints/test_stage_message_decomposition.py`, `tests/integration/test_slack_timebox_buttons.py`, `tests/integration/test_slack_timebox_stage_buttons.py`

**Interfaces:**
- Produces: `retired_cards.RETIRED_ACTION_IDS: tuple[str, ...]` (the seven ids), `retired_cards.RETIRED_CARD_TEXT: str`, `async def retire_card(*, client, body) -> None`. `timeboxing_commit.encode_metadata(values: dict[str, str]) -> str` and `decode_metadata(payload: str) -> dict[str, str]` (moved verbatim from `constraint_review.py:487-497`, so Task 6 can delete that module).

- [ ] **Step 1: Write the failing test**

```python
"""A button from a retired flow says so, instead of failing forever.

The legacy agent's confirm/undo and stage cards can still be in Slack history.
Their old dispatchers sent to an agent that no longer exists inside a broad
``except`` that rewrote the card to "please try again" -- a missing agent
would have read as a transient glitch, every time, for as long as the card
existed.
"""

from __future__ import annotations

import pytest

from fateforger.slack_bot import retired_cards
from tests.doubles.slack import RecordingSlackClient


@pytest.mark.parametrize("action_id", retired_cards.RETIRED_ACTION_IDS)
async def test_pressing_a_retired_card_rewrites_it_in_place(action_id):
    client = RecordingSlackClient()
    body = {
        "channel": {"id": "C1"},
        "message": {"ts": "100.1"},
        "actions": [{"action_id": action_id, "value": "anything"}],
    }

    await retired_cards.retire_card(client=client, body=body)

    assert len(client.updates) == 1
    update = client.updates[0]
    assert (update["channel"], update["ts"]) == ("C1", "100.1")
    assert update["text"] == retired_cards.RETIRED_CARD_TEXT
    assert update["blocks"][0]["text"]["text"] == retired_cards.RETIRED_CARD_TEXT


async def test_a_press_without_a_message_is_ignored_not_raised():
    client = RecordingSlackClient()
    await retired_cards.retire_card(client=client, body={"actions": [{}]})
    assert client.updates == []


def test_the_seven_legacy_action_ids_are_all_covered():
    assert set(retired_cards.RETIRED_ACTION_IDS) == {
        "ff_timebox_confirm_submit",
        "ff_timebox_cancel_submit",
        "ff_timebox_undo_submit",
        "ff_timebox_stage_proceed",
        "ff_timebox_stage_back",
        "ff_timebox_stage_redo",
        "ff_timebox_stage_cancel",
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/slack/test_retired_cards.py -q --no-header -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'fateforger.slack_bot.retired_cards'`.

- [ ] **Step 3: Write `retired_cards.py`**

```python
"""One answer for every button the legacy timeboxing agent left in Slack.

The seven action ids below were posted by ``TimeboxingFlowAgent``'s stage and
submit cards. The agent is gone; a press must say so, not fail. The ids are
kept verbatim because Slack will keep sending them for as long as the
messages exist.
"""

from __future__ import annotations

from fateforger.slack_bot.messages import build_text_section_block

FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID = "ff_timebox_confirm_submit"
FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID = "ff_timebox_cancel_submit"
FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID = "ff_timebox_undo_submit"
FF_TIMEBOX_STAGE_PROCEED_ACTION_ID = "ff_timebox_stage_proceed"
FF_TIMEBOX_STAGE_BACK_ACTION_ID = "ff_timebox_stage_back"
FF_TIMEBOX_STAGE_REDO_ACTION_ID = "ff_timebox_stage_redo"
FF_TIMEBOX_STAGE_CANCEL_ACTION_ID = "ff_timebox_stage_cancel"

RETIRED_ACTION_IDS: tuple[str, ...] = (
    FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID,
    FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID,
    FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID,
    FF_TIMEBOX_STAGE_PROCEED_ACTION_ID,
    FF_TIMEBOX_STAGE_BACK_ACTION_ID,
    FF_TIMEBOX_STAGE_REDO_ACTION_ID,
    FF_TIMEBOX_STAGE_CANCEL_ACTION_ID,
)

RETIRED_CARD_TEXT = (
    "This card is from a retired planning flow and its buttons no longer do "
    "anything. Start again with /timebox."
)


async def retire_card(*, client, body: dict) -> None:
    """Rewrite the pressed message in place; a press with no message is a no-op."""
    channel_id = (body.get("channel") or {}).get("id") or ""
    message_ts = (body.get("message") or {}).get("ts") or ""
    if not (channel_id and message_ts):
        return
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=RETIRED_CARD_TEXT,
        blocks=[build_text_section_block(text=RETIRED_CARD_TEXT)],
    )
```

Check `build_text_section_block` exists in `fateforger.slack_bot.messages` (the deleted dispatchers imported it from there); if it lives elsewhere, import it from where it is.

- [ ] **Step 4: Move the metadata helpers and delete the coordinator**

In `src/fateforger/slack_bot/timeboxing_commit.py`:
- delete `from fateforger.slack_bot.constraint_review import decode_metadata, encode_metadata` (line 15) and add `from urllib.parse import parse_qs, urlencode`;
- paste `encode_metadata` and `decode_metadata` verbatim from `constraint_review.py:487-497` above `class TimeboxCommitMeta`;
- delete `class TimeboxingCommitCoordinator` (line 362 to the line before `def _slack_payload_from_result`) and `_slack_payload_from_result` itself (line 545 to end of file);
- delete the now-unused imports (`TimeboxingCommitDate`, and whatever the coordinator alone used — run the file through `python -m pyflakes` or read the import block).

`git rm src/fateforger/slack_bot/timeboxing_submit.py src/fateforger/slack_bot/timeboxing_stage_actions.py`.

- [ ] **Step 5: Rewire `handlers.py`**

- Imports: remove `TimeboxingCommitCoordinator` from the `timeboxing_commit` block; delete the `timeboxing_stage_actions` and `timeboxing_submit` import blocks entirely; add `from fateforger.slack_bot import retired_cards`.
- Delete the three `timeboxing_commit = ...`, `timeboxing_submit = ...`, `timeboxing_stage_actions = ...` constructions (`~3567-3571`).
- At `~4488` and `~4608`, delete the `if _timebox_backend() == "legacy": await timeboxing_commit.handle_*(...); return` blocks (the harness call that follows is now unconditional).
- Replace the seven `@app.action(FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID)` … `@app.action(FF_TIMEBOX_STAGE_CANCEL_ACTION_ID)` handlers (`~4625-4700`) with:

```python
    async def _on_retired_card(ack, body, client, logger):
        await ack()
        await retired_cards.retire_card(client=client, body=body)

    for _retired_id in retired_cards.RETIRED_ACTION_IDS:
        app.action(_retired_id)(_on_retired_card)
```

- [ ] **Step 6: Trim the boundary test and delete the dead test files**

In `tests/unit/slack/test_slack_boundary_withholds_tool_results.py`, the parametrization at `~158-163` names four payload builders; keep `_slack_payload_from_result` (handlers) only — delete the `_commit_payload`, `_stage_payload`, `_submit_payload` entries, their `ids`, and the imports that defined them (`~26-30` and the `timeboxing_commit` one). Update the comment above it: three of the four copies are gone with the legacy flow; the parametrization stays so a second copy is caught.

```bash
git rm -q tests/unit/timeboxing/test_timeboxing_submit_flow.py tests/unit/timeboxing/test_timeboxing_stage_actions.py tests/unit/constraints/test_stage_message_decomposition.py tests/integration/test_slack_timebox_buttons.py tests/integration/test_slack_timebox_stage_buttons.py
```

- [ ] **Step 7: Run the suite**

Run the Global Constraints test command. Expected: green except the environmental failure and `test_runtime_registers_no_timeboxing_agent`. `grep -rn "timeboxing_submit\|timeboxing_stage_actions\|TimeboxingCommitCoordinator" src tests` must be empty.

- [ ] **Step 8: Commit**

```bash
git add src/fateforger/slack_bot/retired_cards.py src/fateforger/slack_bot/timeboxing_commit.py src/fateforger/slack_bot/handlers.py tests/unit/slack/test_retired_cards.py tests/unit/slack/test_slack_boundary_withholds_tool_results.py
git commit -m "feat(slack): a legacy card button says the flow is retired

The stage and submit cards' dispatchers sent to timeboxing_agent inside
a broad except that rewrote the card to 'please try again'. With the
agent gone that would have been a transient glitch forever. One handler
now answers all seven action ids honestly.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Port the consecutive-no-progress cap into the kernel

Legacy commit `9eb333e` capped consecutive refine passes that changed nothing at three. The harness's `NeedsAnotherTurn` is uncapped: a planner that asks for another turn every time is the "twelve minutes of Proceeding…" shape that fix was written for. `HandledInteraction.outcome_kind` already records every outcome, so the streak is derived from the snapshot — no new field.

**Files:**
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` — `_another_turn` (`~1446`) and its two callers (`~1375`, `~1443`)
- Create: `tests/unit/timeboxing/test_another_turn_is_capped.py`

**Interfaces:**
- Consumes: `tests.doubles.timeboxing` (`RecordedPlanner`, `RecordingProgressSink`, `_advance_request(*, expected_revision=3)`, `_incident_snapshot()`, `_kernel(repo, planner, *, context=None, commit=None)`), `InMemoryPlanningSessionRepository`, `PlanningResult`, `PlannerContinuation`, `NeedsAnotherTurn`, `TurnFailed`.
- Produces: `adaptive_timeboxing.MAX_CONSECUTIVE_CONTINUATIONS: int = 3`; a `TurnFailed(code="no_progress", message=...)` on the third consecutive continuation.

- [ ] **Step 1: Write the failing tests**

```python
"""A planner that keeps asking for another turn is stopped, and told so.

Legacy commit 9eb333e capped consecutive no-change refine passes at three;
the harness's NeedsAnotherTurn had no cap. The streak is read from the
snapshot's handled_interactions, which already record every outcome kind.
"""

from __future__ import annotations

import pytest

from fateforger.agents.timeboxing import adaptive_timeboxing as kernel_module
from fateforger.agents.timeboxing.adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
)
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    ArtifactKind,
    NeedsAnotherTurn,
    PlannerContinuation,
    PlanningResult,
    TurnFailed,
)
from tests.doubles.timeboxing import (
    RecordedPlanner,
    RecordingProgressSink,
    _advance_request,
    _incident_snapshot,
    _kernel,
)

_REASON = "lunch still collides with the daily; shortening it next pass"
LIMIT = kernel_module.MAX_CONSECUTIVE_CONTINUATIONS


def _continuing_planner():
    return RecordedPlanner(PlanningResult(continuation=PlannerContinuation(reason=_REASON)))


async def _turns(kernel, count: int, *, first_revision: int = 3):
    outcomes = []
    for i in range(count):
        outcomes.append(
            await kernel.turn(
                _advance_request(expected_revision=first_revision + i),
                progress=RecordingProgressSink(),
            )
        )
    return outcomes


def test_the_limit_allows_an_ordinary_continuation_but_not_a_loop():
    assert 2 < LIMIT < 9


async def test_continuations_accumulate_then_fail_on_the_limit():
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    kernel = _kernel(repo, _continuing_planner())

    outcomes = await _turns(kernel, LIMIT)

    assert all(isinstance(o, NeedsAnotherTurn) for o in outcomes[:-1])
    last = outcomes[-1]
    assert isinstance(last, TurnFailed)
    assert last.code == "no_progress"
    assert _REASON in last.message


async def test_the_failure_says_how_many_passes_made_no_progress():
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    (*_, last) = await _turns(_kernel(repo, _continuing_planner()), LIMIT)
    assert str(LIMIT) in last.message


async def test_a_productive_turn_resets_the_streak():
    """Two continuations, then an artifact, then two more: no failure."""
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    skeleton = ArtifactDraft(
        kind=ArtifactKind.SKELETON,
        payload={"markdown": "## Saturday\n- 10:00 Deep work"},
        dependency_revisions={"planning_day": 1},
    )
    scripted = [
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
        PlanningResult(artifact_updates=[skeleton]),
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
    ]

    class _Scripted(RecordedPlanner):
        async def produce(self, brief, progress):
            self.briefs.append(brief)
            return scripted[len(self.briefs) - 1]

    outcomes = await _turns(_kernel(repo, _Scripted(scripted[0])), len(scripted))

    assert not any(isinstance(o, TurnFailed) for o in outcomes), outcomes
```

Note: the productive turn returns `AwaitingApproval` for the skeleton; the next `Advance` with the skeleton unapproved may be refused by the kernel with its own `TurnFailed`. If `test_a_productive_turn_resets_the_streak` fails with a code other than `no_progress`, that is the test needing an `ApproveArtifact` intent between turns three and four, not the cap being wrong — read `tests/unit/timeboxing/test_adaptive_timeboxing.py` for how approval is driven there and add it. The assertion to keep is `code != "no_progress"`.

- [ ] **Step 2: Run to verify they fail**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/timeboxing/test_another_turn_is_capped.py -q --no-header -p no:cacheprovider`
Expected: FAIL with `AttributeError: module ... has no attribute 'MAX_CONSECUTIVE_CONTINUATIONS'`.

- [ ] **Step 3: Implement the cap**

In `adaptive_timeboxing.py`, next to the other module constants:

```python
#: How many times in a row the planner may ask for another turn before the
#: session says it is looping. Legacy's `_REFINE_NO_CHANGE_LIMIT` (9eb333e):
#: a real session once ran nine identical passes before anyone noticed.
MAX_CONSECUTIVE_CONTINUATIONS = 3
```

Replace `_another_turn`:

```python
    def _another_turn(
        self, snapshot: PlanningSessionSnapshot, result: PlanningResult
    ) -> NeedsAnotherTurn | TurnFailed:
        """Let the planner continue, until continuing is all it does.

        Logged at warning because a planner that asks every turn is a bug,
        and a silent continuation is indistinguishable from slow progress --
        which is how a loop would hide. The streak is the run of
        ``needs_another_turn`` outcomes at the tail of the session's handled
        interactions; this turn would be one more.
        """

        assert result.continuation is not None
        reason = result.continuation.reason
        streak = 1
        for handled in reversed(snapshot.handled_interactions):
            if handled.outcome_kind != "needs_another_turn":
                break
            streak += 1
        if streak >= MAX_CONSECUTIVE_CONTINUATIONS:
            logger.warning(
                "planner asked for another turn %d times in a row; failing the turn reason=%s",
                streak,
                reason,
            )
            return TurnFailed(
                code="no_progress",
                message=(
                    f"The planner asked for another turn {streak} times in a row "
                    f"without finishing. Last reason: {reason}"
                ),
            )
        logger.warning("planner asked for another turn reason=%s", reason)
        return NeedsAnotherTurn(reason=reason)
```

Update both callers to pass the snapshot: at `~1375` `self._another_turn(snapshot, result)` and at `~1443` `self._another_turn(snapshot, result)` (the local there may be named `updated` — pass the snapshot the turn started from, so the streak counts prior turns only; either name works because `handled_interactions` is unchanged within the turn).

- [ ] **Step 4: Run the new tests, then the existing another-turn tests**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/timeboxing/test_another_turn_is_capped.py tests/unit/timeboxing/test_planner_can_ask_for_another_turn.py tests/unit/timeboxing/test_adaptive_timeboxing.py -q --no-header -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Break it on purpose**

Set `MAX_CONSECUTIVE_CONTINUATIONS = 99`, run the new file: `test_continuations_accumulate_then_fail_on_the_limit` and `test_the_failure_says_how_many_passes_made_no_progress` must FAIL. Restore `3`. A test that passes on the first run has not yet earned trust.

- [ ] **Step 6: Commit**

```bash
git add src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/unit/timeboxing/test_another_turn_is_capped.py
git commit -m "feat(timeboxing): a planner that keeps asking for another turn is stopped

Legacy capped consecutive no-change refine passes at three (9eb333e); the
kernel's NeedsAnotherTurn had no cap, and _another_turn's own docstring
named the loop it could not see. The streak is read from the outcome
kinds the snapshot already records, so nothing new is stored.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Delete the agent, its registration, the flag, and the orphaned subtree

**Files:**
- Modify: `src/fateforger/core/runtime.py:31` (import), `:798-802` (`TimeboxingFlowAgent.register` block)
- Modify: `src/fateforger/slack_bot/handlers.py` — `_timebox_backend` (`~1422`), its five remaining call sites (`~2871`, `~3175`, `~3253`, `~4028`; `~4488`/`~4608` went in Task 3), `_build_timeboxing_message` + `_build_agent_message`'s timeboxing branch (`~534-590`), the `StartTimeboxing, TimeboxingUserReply` import (`:34`), the `os` import if now unused
- Modify: `tests/conftest.py:76-88` (delete the autouse fixture), `:14` and `:29` (the `AdmonisherBase` import and `create_all` stay — `admonisher/models.py` survives)
- Modify: `src/fateforger/agents/admonisher/__init__.py` — empty the re-exports
- Modify: `src/fateforger/agents/timeboxing/mcp_clients.py` — delete `class CalendarDaySnapshot` (`:30-36`) and `class McpCalendarClient` (`:323` to end of file); delete imports only they used
- Modify: `src/fateforger/slack_bot/dsh_commit_gate_hook.py:4-10` docstring
- Delete: `src/fateforger/agents/timeboxing/agent.py` and the orphaned modules (list in Step 3)
- Delete: the test files in Step 5
- Modify: `tests/unit/slack/test_schedular_routes_to_harness.py` (delete `test_the_legacy_flow_is_still_reachable` and the eleven tests that drive `_harness_turn` — see Step 6), `tests/unit/slack/test_timebox_backend_routing.py` (delete file), the five files that set `FF_TIMEBOX_BACKEND`

**Interfaces:**
- Consumes: `/private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/8947bec9-0516-4f69-85cd-89dd1fed17f3/scratchpad/spike_tools/orphans.py` — copy it to `scripts/dev/tests/orphans.py` in Step 1; it answers "which `src/` modules does no entry point reach". Its `ENTRIES` rule must list, by name, the out-of-process MCP servers (`fateforger.slack_bot.task_board_mcp`, `fateforger.slack_bot.timebox_progress_mcp`), the DSH hooks (`fateforger.slack_bot.dsh_timebox_attempt_guard_hook`, `dsh_progress_hook`, `dsh_commit_gate_hook`), `memory.backfill`, and `tmbx.journal.read_api`, or it reports ten false orphans at HEAD.
- Produces: `test_runtime_registers_no_timeboxing_agent` green; `grep -rn "TimeboxingFlowAgent\|FF_TIMEBOX_BACKEND\|_timebox_backend\|_harness_turn" src/` empty.

- [ ] **Step 1: Bring the reachability tool into the repo and take the "before" reading**

```bash
mkdir -p scripts/dev/tests
cp /private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/8947bec9-0516-4f69-85cd-89dd1fed17f3/scratchpad/spike_tools/orphans.py scripts/dev/tests/orphans.py
cp /private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/8947bec9-0516-4f69-85cd-89dd1fed17f3/scratchpad/spike_tools/dangling.py scripts/dev/tests/dangling.py
```

Open `scripts/dev/tests/orphans.py`, find its entry-point rule, and add the explicit names above. Run `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python scripts/dev/tests/orphans.py` from the worktree root. Expected: **0 orphans** (if it still reports any, they are entry points the rule is missing — add them; do not delete them). Also run `dangling.py` and note its output; it lists modules importing something that does not exist.

- [ ] **Step 2: Cut the registration and the flag**

`runtime.py`: delete line 31 and the four-line `await TimeboxingFlowAgent.register(...)` block.

`handlers.py`:
- delete `def _timebox_backend()` and its docstring;
- `~2871`: `if _timebox_backend() != "legacy":` → unconditional; delete the `else:` branch (the `handoff_msg = _build_agent_message(...)` / `runtime.send_message(... "timeboxing_agent" ...)` block);
- `~3175`: `if would_alias_root and _timebox_backend() != "legacy":` → `if would_alias_root:`;
- `~3253`: `primary_harness_turn = (agent_type == "timeboxing_agent" and _timebox_backend() != "legacy")` → `primary_harness_turn = agent_type == "timeboxing_agent"`;
- `~4028`: `if _timebox_backend() != "legacy":` → unconditional; delete its `else:` branch if one exists;
- delete `_build_timeboxing_message` and, in `_build_agent_message`, the `if agent_type == "timeboxing_agent": return _build_timeboxing_message(...)` branch so it always returns `TextMessage(content=cleaned_text, source=user)`; delete the `StartTimeboxing, TimeboxingUserReply` import;
- if `os` is now unreferenced in `handlers.py`, delete `import os`.

`tests/conftest.py`: delete `_timebox_backend_is_legacy_unless_asked` entirely (lines `76-88`). Do not replace it — the spike measured the suite identical without it; what keeps a fake runtime in-process is `_run_adaptive_timebox_turn`'s "not wired" early return.

Strip `FF_TIMEBOX_BACKEND` from the tests that still set or mention it: the `monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")` lines Task 2 added, `tests/unit/slack/test_timebox_session_surface.py:87` (and its docstring sentence about the pin), `tests/e2e/test_slack_timebox_command.py:147`, `tests/unit/slack/test_schedular_routes_to_harness.py:267-270`, `tests/unit/timeboxing/test_harness_approval_action.py` and `tests/unit/timeboxing/test_slack_timeboxing_channel_redirect.py` (grep `FF_TIMEBOX_BACKEND` — comments included). `git rm tests/unit/slack/test_timebox_backend_routing.py` (it imports `_timebox_backend` at module level; its body tests all moved to `test_timebox_bare_command.py` in PR #396).

- [ ] **Step 3: Delete the agent and iterate the reachability tool to zero**

```bash
git rm -q src/fateforger/agents/timeboxing/agent.py
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python scripts/dev/tests/orphans.py
```

Delete everything it reports, rerun, repeat until it reports zero. The spike measured the expected set — **32 modules** on the first pass, then two more once `admonisher/__init__.py` is emptied:

```
fateforger/agents/shared/handoff_policy.py
fateforger/agents/timeboxing/{calendar_reconciliation,constants,constraint_memory_component,
  constraint_reconciliation,constraint_retriever,constraint_search_tool,contracts,flow_graph,
  nlu,notion_constraint_extractor,patching,planning_aspects,planning_policy,prompt_rendering,
  pydantic_parsing,scheduler_prefetch_capability,stage_gating,submitter,sync_engine,
  task_marshalling_capability,tb_ops,tool_result_presenter,toon_views}.py
fateforger/agents/timeboxing/nodes/            (the package)
fateforger/llm/toon.py
fateforger/sync_core/                          (the package)
tmbx/journal/{instrument,constraint_refs}.py
```

Then the already-dead set PR #396 flagged: `fateforger/agents/admonisher/{base,calendar,commitment}.py`, `fateforger/agents/schedular/diffing_agent.py`, `fateforger/agents/timeboxing/{flow,prompts,state,notebook_entrypoints}.py`, `fateforger/slack_bot/{relay_agent,topics}.py`, `fateforger/tools_config/`. **Before** deleting the three admonisher modules, replace `src/fateforger/agents/admonisher/__init__.py` with a two-line docstring and an empty `__all__: list[str] = []` — `runtime.py:26` and `tests/conftest.py:14` import `admonisher.agent` / `.models` through that package. Rerun `orphans.py`: it will now report `fateforger.core.logging` and `fateforger.core.slack` (only `admonisher/base.py` imported them). Delete those too.

In `mcp_clients.py` delete `class CalendarDaySnapshot` and `class McpCalendarClient` (line 323 to the end), then any import only they used. `ConstraintMemoryClient` stays.

If `orphans.py` reports a module **not** in the lists above, stop and check it against `grep -rn "<name>" src scripts` before deleting — it is either a leak in the entry-point rule or something the spike did not see.

- [ ] **Step 4: Fix the docstrings that will lie**

`src/fateforger/slack_bot/dsh_commit_gate_hook.py:4-10` says "The harness path has no review stage — unlike the legacy flow". Replace that sentence with: *"The harness gate denies by default; stages 4 and 5 are the review and commit cards, and only an explicit approval press opens it."* Keep the rest of the reasoning.

- [ ] **Step 5: Delete the tests whose subject is gone**

Run the suite once; collection errors name the files. Expected: these **23** (import only deleted modules):

```
tests/integration/test_timeboxing_durable_constraint_retriever_wiring.py
tests/unit/constraints/test_constraint_auto_promotion.py
tests/unit/constraints/test_constraint_frame_slot_normalisation.py
tests/unit/constraints/test_constraint_memory_component.py
tests/unit/constraints/test_constraint_reconciliation.py
tests/unit/constraints/test_constraint_retriever.py
tests/unit/constraints/test_constraint_search_tool.py
tests/unit/constraints/test_timeboxing_constraint_retriever_startup_prefetch.py
tests/unit/core/test_handoff_policy.py
tests/unit/core/test_sync_submit_baseline_guard.py
tests/unit/core/test_toon_encode.py
tests/unit/timeboxing/test_timeboxing_calendar_prefetch_feedback.py
tests/unit/timeboxing/test_timeboxing_constants.py
tests/unit/timeboxing/test_timeboxing_graph_turn_lock.py
tests/unit/timeboxing/test_timeboxing_graphflow_state_machine.py
tests/unit/timeboxing/test_timeboxing_memory_backend_selection.py
tests/unit/timeboxing/test_timeboxing_planning_date_timeout.py
tests/unit/timeboxing/test_timeboxing_pydantic_parsing.py
tests/unit/timeboxing/test_timeboxing_refine_tool_orchestration.py
tests/unit/timeboxing/test_timeboxing_scheduler_prefetch_capability.py
tests/unit/timeboxing/test_timeboxing_session_logging.py
tests/unit/timeboxing/test_timeboxing_stateless_agents.py
tests/unit/tmbx/test_constraint_refs.py
```

and these, whose subject is a deleted module even though they also import live ones (the spike classified each; `tests/unit/timeboxing/test_timeboxing_task_marshalling_capability.py` tests the deleted capability, and `test_sync_reconciliation_summary.py`'s function is a wrapper over the deleted `calendar_reconciliation`, which settles the spec's open question — it goes):

```
tests/unit/constraints/test_constraint_extraction_reason.py
tests/unit/constraints/test_constraint_extractor_tool.py
tests/unit/constraints/test_constraint_nlu_frame_slot.py
tests/unit/constraints/test_constraint_relevance_filter.py
tests/unit/constraints/test_timeboxing_constraint_dedupe.py
tests/unit/constraints/test_timeboxing_constraint_priority.py
tests/unit/constraints/test_timeboxing_constraint_selection.py
tests/unit/constraints/test_timeboxing_durable_constraints.py
tests/unit/constraints/test_timeboxing_stage_message_constraint_context.py
tests/unit/timeboxing/test_agent_journal_wiring.py
tests/unit/timeboxing/test_calendar_reconciliation.py
tests/unit/timeboxing/test_calendar_submitter.py
tests/unit/timeboxing/test_memory_surface.py
tests/unit/timeboxing/test_patching.py
tests/unit/timeboxing/test_phase4_rewiring.py
tests/unit/timeboxing/test_skeleton.py
tests/unit/timeboxing/test_stage_decisions.py
tests/unit/timeboxing/test_stage_prompts.py
tests/unit/timeboxing/test_sync_engine.py
tests/unit/timeboxing/test_sync_reconciliation_summary.py
tests/unit/timeboxing/test_tb_ops.py
tests/unit/timeboxing/test_timeboxing_commit_skips_initial_extraction.py
tests/unit/timeboxing/test_timeboxing_mcp_calendar_client.py
tests/unit/timeboxing/test_timeboxing_prompt_rendering.py
tests/unit/timeboxing/test_timeboxing_refine_loop_cap.py
tests/unit/timeboxing/test_timeboxing_refine_renders_schedule.py
tests/unit/timeboxing/test_timeboxing_remote_snapshot_plan.py
tests/unit/timeboxing/test_timeboxing_review_submit_prompt.py
tests/unit/timeboxing/test_timeboxing_session_init_order.py
tests/unit/timeboxing/test_timeboxing_stage1_deterministic_fast_path.py
tests/unit/timeboxing/test_timeboxing_stage3_markdown_block.py
tests/unit/timeboxing/test_timeboxing_stage_message_template_coverage.py
tests/unit/timeboxing/test_timeboxing_task_marshalling_capability.py
tests/unit/timeboxing/test_timeboxing_task_prefetch_context.py
tests/unit/timeboxing/test_timeboxing_thread_reply_kickoff.py
tests/unit/timeboxing/test_timeboxing_tool_result_presenter.py
tests/unit/tmbx/test_instrument.py
```

`git rm -q` each. **Do not delete** `tests/unit/constraints/test_kg_constraint_client.py` or `tests/unit/tmbx/test_patch_order_is_preserved.py` — Task 7 rewires them.

- [ ] **Step 6: Trim `test_schedular_routes_to_harness.py`**

`_harness_turn` had zero callers in `src/` before this work; the spike proved it and Step 2's grep will confirm it. Delete `_harness_turn` and `_owned_harness_ask` from `handlers.py` (grep `_owned_harness_ask` first: its only caller is `_harness_turn`). In the test file keep exactly `test_both_entry_points_reach_the_harness` and `test_the_handoff_interception_uses_the_redirected_thread`, plus whatever module-level helpers they use; delete the rest (the `_harness` fixture, `_Reply`, and every test that calls `handlers._harness_turn`). Rewrite the module docstring to say what the two remaining tests guard: that every door into timeboxing reaches the kernel, keyed by its own thread.

- [ ] **Step 7: Run the suite; measure**

Run the Global Constraints test command. Expected: **collection clean**; failures limited to the environmental one and whatever `test_kg_constraint_client.py` / `test_patch_order_is_preserved.py` raise at runtime (Task 7). `test_runtime_registers_no_timeboxing_agent` now passes. Note the wall-clock; the spike measured ~29s.

Verify:
```bash
grep -rn "TimeboxingFlowAgent\|FF_TIMEBOX_BACKEND\|_timebox_backend\|_harness_turn\|_owned_harness_ask" src/   # must be empty
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python scripts/dev/tests/orphans.py                          # must be 0
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests -m slow --collect-only -q | tail -1  # 89 collected
```

- [ ] **Step 8: Commit**

```bash
git add -A src/ tests/ scripts/dev/tests/
git commit -m "refactor: retire TimeboxingFlowAgent and the 34 modules only it reached

Off everywhere since 2026-08-22, registered unconditionally at startup
with nothing routed to it. The harness covers all five stages, the
confirm gate (stronger: deny by default), Undo, and the migrated
constraint store; the one behaviour it lacked landed in the previous
commit. The reachability tool reports zero orphans after the cut; the
identifier 'timeboxing_agent' stays where it is a handoff target, a
persona key, or a session-key prefix.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Remove the constraint store that only the legacy agent wrote

`preferences.ConstraintStore` is a sqlite table; its only writers were `agent.py` and the review modal only legacy posted. After Task 5 the table is permanently empty and the harness path still reads it after every turn — a no-op that drags a whole plumbing chain through `handlers.py`.

**Files:**
- Modify: `src/fateforger/agents/timeboxing/preferences.py` — delete `class ConstraintStore` (`:102` to its end) and `ensure_constraint_schema`; keep `Constraint`, `ConstraintBase`, `ConstraintStatus`, `ConstraintScope`, `ConstraintNecessity`, `ConstraintSource` (`messages.py:10`, `handlers.py`, `constraint_review.py` type against them — the last of those goes below)
- Delete: `src/fateforger/slack_bot/constraint_review.py` (its metadata helpers moved in Task 3)
- Modify: `src/fateforger/slack_bot/handlers.py` — the `constraint_review` import block (`~56-66`), `ConstraintStore`/`ensure_constraint_schema` from the `preferences` import (`:35-40`), `_build_timeboxing_thread_root_blocks` (`~292-330`), `_maybe_update_timeboxing_thread_constraints` (`~338-383`), the nested `_update_constraints` (`~2588-2611`) and its three call sites (`~3011`, `~3155`, `~3545`), the `get_constraint_store` parameter on `route_slack_event` (`~2576`), `_route_command_as_message` (`~2357`), `_handle_timebox_command` (`~2404`), `_handle_task_refine_command` (`~2451`) and their pass-throughs (`~2400`, `~2440`, `~2486`, `~3765`, `~4103`, `~4117`), `constraint_store: ConstraintStore | None = None` and `_get_constraint_store` (`~3563`, `~3619-3630`), the four review handlers (`~4700-4800`)
- Modify: `tests/unit/timeboxing/test_stage_receipts_in_the_turn.py:353-415`
- Delete: `tests/unit/constraints/test_slack_constraint_review.py`, `tests/unit/constraints/test_slack_constraint_review_all_action.py`, `tests/unit/constraints/test_notion_constraint_store_schema_compat.py` only if it imports `ensure_constraint_schema` (check), and any test in `tests/unit/constraints/` whose only subject is `ConstraintStore` (`test_timeboxing_constraint_store_canonicalization.py`, `test_timeboxing_constraint_store_shared_scopes.py`, `test_durable_constraint_store.py` if it builds a `ConstraintStore` — read each header; a file that tests `DurableConstraintStore` over the kg client stays)

**Interfaces:**
- Consumes: nothing new.
- Produces: `route_slack_event(*, runtime, focus, default_agent, event, bot_user_id, say, client, planning=None, acked=None)` — `get_constraint_store` gone. `grep -rn "ConstraintStore\b" src/` matches only `memory/constraint_store.py` and `durable_constraint_store.py`'s own classes.

- [ ] **Step 1: Delete the review handlers and the thread-constraints redraw**

In `handlers.py`, in this order, running the test file named in each bullet after it:
1. Delete the four `@app.action(FF_CONSTRAINT_REVIEW_ALL_ACTION_ID)` / `LEGACY_...` / `CONSTRAINT_ROW_REVIEW_ACTION_ID` / `@app.view(CONSTRAINT_REVIEW_VIEW_CALLBACK_ID)` handlers and `_handle_constraint_review_all_action`; delete the `constraint_review` import block. `git rm src/fateforger/slack_bot/constraint_review.py tests/unit/constraints/test_slack_constraint_review.py tests/unit/constraints/test_slack_constraint_review_all_action.py`.
2. Delete `_maybe_update_timeboxing_thread_constraints`, `_build_timeboxing_thread_root_blocks`, the nested `_update_constraints`, and its three `await _update_constraints(...)` call sites.
3. Remove the `get_constraint_store` parameter from `route_slack_event`, `_route_command_as_message`, `_handle_timebox_command`, `_handle_task_refine_command`, and every `get_constraint_store=...` keyword at their call sites; delete `_get_constraint_store` and the `constraint_store: ConstraintStore | None = None` nonlocal; drop `ConstraintStore, ensure_constraint_schema` from the `preferences` import and `create_async_engine`/`async_sessionmaker`/`_coerce_async_database_url` if now unused.
4. In `preferences.py` delete `class ConstraintStore` and `ensure_constraint_schema`.

- [ ] **Step 2: Retarget the stage-receipts test**

`tests/unit/timeboxing/test_stage_receipts_in_the_turn.py`, `test_a_typed_day_change_survives_the_constraints_redraw` (`~366`): the redraw it guarded against is gone, which is the outcome it wanted. Delete the `await handlers._maybe_update_timeboxing_thread_constraints(...)` call and the `_NoConstraints` class, rename the test `test_a_typed_day_change_is_what_the_root_shows`, and rewrite the comment block above it (`~353-360`) to two lines: the root used to be redrawn from the focus label by a constraints refresh after every turn, which overwrote a typed day change; that refresh no longer exists, and this pins that the relabel is the last write.

- [ ] **Step 3: Run the suite**

Run the Global Constraints test command. Expected: green except the environmental failure and the two Task 7 files. `grep -rn "get_constraint_store\|_update_constraints\|constraint_review\|ensure_constraint_schema" src/ tests/` must be empty.

- [ ] **Step 4: Commit**

```bash
git add -A src/fateforger/slack_bot src/fateforger/agents/timeboxing/preferences.py tests/
git commit -m "refactor(slack): drop the constraint store only the legacy agent wrote

Its writers were agent.py and a review modal only legacy posted. After
the retirement the table is permanently empty and the harness path
re-read it after every turn -- a no-op dragging get_constraint_store
through four handler signatures. ConstraintStore now names one class in
the repo, the memory server's.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Rewire the two live tests that borrowed a helper from a deleted module

**Files:**
- Modify: `tests/unit/constraints/test_kg_constraint_client.py:168-190`
- Modify: `tests/unit/tmbx/test_patch_order_is_preserved.py:65` and `:387`

**Interfaces:** none new.

- [ ] **Step 1: `test_kg_constraint_client.py`**

`test_the_rows_survive_the_reconciliation_the_agent_runs` (`~168`) imports `reconcile_constraint_rows` from the deleted `constraint_reconciliation` inside its body. The risk it names — rows shaped so that a reader drops them silently — is real, but the reader that runs now is `build_durable_constraint_store` (`runtime.py:605`) over `KGConstraintMemoryClient`. Rewrite the test body to:

```python
def test_the_rows_survive_the_reader_the_harness_runs(tmp_path):
    """Rows shaped wrongly are dropped in silence -- the failure mode the
    Notion backend had. The harness reads through the durable store adapter,
    so that is the reader that must see every row."""
    import asyncio

    from fateforger.agents.timeboxing.durable_constraint_store import (
        build_durable_constraint_store,
    )

    db = _store_with(tmp_path, _constraint(), _constraint(name="Commute duration"))
    store = build_durable_constraint_store(KGConstraintMemoryClient(db))
    rows = asyncio.run(store.query_constraints(filters={}, limit=50))

    assert sorted(r["name"] for r in rows) == sorted(
        [_constraint().name, "Commute duration"]
    )
```

Check `build_durable_constraint_store`'s signature and `query_constraints`'s keyword names in `durable_constraint_store.py` before relying on the shape above; match them. Run the file; 17 pass.

- [ ] **Step 2: `test_patch_order_is_preserved.py`**

Line 65 imports `_ops_json` from the deleted `tmbx.journal.instrument`; line 387 iterates `for serialised in (patch.model_dump_json(), _ops_json(patch)):`. `_ops_json` was `patch.model_dump_json()` with a fallback. Delete the import and change line 387 to `for serialised in (patch.model_dump_json(),):` — or unroll the loop if it reads better. Run the file; 14 pass.

- [ ] **Step 3: Full suite, wall-clock, coverage diff**

Run the Global Constraints test command; expected green except the environmental failure. Record the pass count and wall-clock (gate: within 10% of 31.6s).

Then the per-line coverage diff:
```bash
SP=/private/tmp/claude-501/-Users-hugoevers-VScode-projects-admonish-1/8947bec9-0516-4f69-85cd-89dd1fed17f3/scratchpad
cp $SP/covdiff.py scripts/dev/tests/covdiff.py
cp $SP/taxonomy.py scripts/dev/tests/taxonomy.py   # the seam classification, so a PR's numbers are measured
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests -m "not slow" -q --no-header -p no:cacheprovider --cov=src --cov-report=json:$SP/cov_after_retire.json > /dev/null 2>&1
/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python scripts/dev/tests/covdiff.py cov_final.json cov_after_retire.json
```
`cov_final.json` is PR #396's after-state, already in that scratchpad. Expected: every file that lost a covered line is a **deleted** file (they appear because the before-run measured them). Any surviving file that lost a line is a regression: name it in the report. The known exception, `timeboxing_session_store.py:157`, is a race the concurrent-save test wins nondeterministically.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/constraints/test_kg_constraint_client.py tests/unit/tmbx/test_patch_order_is_preserved.py scripts/dev/tests/covdiff.py
git commit -m "test: rewire the two live tests that borrowed a helper from retired code

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The guard that keeps the suite honest

The spec's rule: a test reaches its subject through its public constructor or a port — never `__new__`, never a private assignment on a non-`self` target, on the subject *or on a double*. Lands here with the allowlist the cut leaves, so project 2 starts from a measured list rather than a guess.

**Files:**
- Create: `tests/unit/core/test_tests_reach_subjects_honestly.py`
- Create: `tests/honest_allowlist.py`

**Interfaces:**
- Produces: `tests.honest_allowlist.ALLOWED: dict[str, str]` mapping `"<relative test path>::<line>"` to a one-line reason.

- [ ] **Step 1: Write the guard**

```python
"""A test reaches its subject honestly, or says why it cannot.

Two shapes are refused across ``tests/``: ``Class.__new__(Class)``, which
builds an object the constructor would have refused, and ``obj._x = ...``
on any target that is not ``self``, which reaches past a public interface --
on the subject or on a double (a double's state belongs in its ``__init__``).
Each remaining site is listed in ``tests/honest_allowlist.py`` with its
reason. The list only shrinks.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.honest_allowlist import ALLOWED
from tests.repo import ROOT

TESTS = ROOT / "tests"


def _offences() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(TESTS.rglob("*.py")):
        if path.name in {"honest_allowlist.py"} or path == Path(__file__):
            continue
        tree = ast.parse(path.read_text())
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "__new__"
            ):
                found[f"{rel}::{node.lineno}"] = "__new__"
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr.startswith("_")
                        and not target.attr.startswith("__")
                        and not (isinstance(target.value, ast.Name) and target.value.id == "self")
                    ):
                        found[f"{rel}::{node.lineno}"] = f"private write {ast.unparse(target)}"
    return found


def test_every_dishonest_reach_is_allowlisted_with_a_reason():
    offences = _offences()
    unlisted = {k: v for k, v in offences.items() if k not in ALLOWED}
    assert not unlisted, "\n".join(f"{k}  ({v})" for k, v in sorted(unlisted.items()))


def test_the_allowlist_carries_no_dead_entries():
    offences = _offences()
    stale = sorted(k for k in ALLOWED if k not in offences)
    assert not stale, f"remove these, they no longer offend: {stale}"


def test_every_allowlist_entry_says_why():
    assert all(reason.strip() for reason in ALLOWED.values())
```

- [ ] **Step 2: Generate the allowlist from the current tree**

Run the guard once; it fails with the full list. Write `tests/honest_allowlist.py`:

```python
"""Every place a test still reaches past a public interface, and why.

Generated when the legacy agent was retired (2026-09); shrinks in the
composability work that follows. An entry is ``"<path>::<line>": "<reason>"``.
Line numbers move when files are edited -- the guard's second test says which
entries went stale.
"""

ALLOWED: dict[str, str] = {
    # paste the failing list here, one entry per line, each with a reason:
    #   "tests/unit/timeboxing/test_tb_models.py::123": "bypasses chain_must_be_anchored to reach the branch behind it",
}
```

Fill it from the failure output. Reasons must be specific; "legacy" is not a reason. The spike counted about 20 `__new__` sites and a few dozen private writes surviving the cut; if the list is in the hundreds, Task 5 missed deletions — check `orphans.py` again before writing entries.

- [ ] **Step 3: Run; break it on purpose**

Run: `/Users/hugoevers/VScode-projects/admonish-1/.venv/bin/python -m pytest tests/unit/core/test_tests_reach_subjects_honestly.py -q --no-header -p no:cacheprovider` — 3 pass. Then add `x = object.__new__(object)` to any test file, rerun: the first test must FAIL naming that line. Remove it. Delete one allowlist entry, rerun: the first test must FAIL on it. Restore it.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/core/test_tests_reach_subjects_honestly.py tests/honest_allowlist.py
git commit -m "test: a test reaches its subject honestly, or the allowlist says why

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Docs, the spec corrections, and the gate that the suite cannot stand in for

**Files:**
- Modify: `tests/README.md` ("What's out of `tests/unit/`", the doubles list, the run instructions if `tests/coordination` or paths changed)
- Modify: `docs/superpowers/specs/2026-09-08-test-suite-composability-design.md` — record the two deviations
- Modify: `tickets/skeleton_pre_generation.md` — a status line saying the confirm/undo wiring it describes was retired with the legacy agent on this branch, and where Undo lives now (`ff_harness_undo`, `handlers.act_harness_undo`)
- Create: `tickets/harness_bounded_brief_says_what_it_withheld.md`
- Create: `tickets/test_suite_docs_after_legacy_retirement.md`
- Modify: `README_CALENDAR_MCP.md`, `GOOGLE_CALENDAR_MCP_GUIDE.md` only if they instruct constructing `McpCalendarClient` — add a superseded line pointing at `tmbx/calendar/`

**Interfaces:** none.

- [ ] **Step 1: The spec corrections**

In the spec's "Scope of the delete" section, replace the `mcp_clients.py whole` bullet with: *`McpCalendarClient` and `CalendarDaySnapshot` leave `mcp_clients.py`; `ConstraintMemoryClient` stays — it is the default `tasks_defaults_memory_backend` with tests of its own.* Replace the `settings.timeboxing_memory_backend` bullet with: *Kept. It is also read by `runtime.py`'s graphiti startup checks and by tasks' defaults memory; only `agent.py`'s branch on it goes.* In Project 2's "Injection — none" table, the `ConstraintMemoryClient` row changes from "deleted in project 1" to "survives; its `workbench=` seam is the one the spike found earns its keep (0.5s → 17.9s without it) — project 2 adds it."

- [ ] **Step 2: The latent-gap ticket**

`tickets/harness_bounded_brief_says_what_it_withheld.md`:

```markdown
# Ticket: when the planning brief is bounded, it must say what it withheld

## Tracking
- Status: Open, not started. Filed from the legacy-agent retirement (2026-09-09).
- Not blocking: the harness caps nothing today.

## Why
Legacy commit 3dea6ae added "N lower-priority constraints did not fit this
pass" after constraints were dropped silently -- the third time that rule was
rediscovered (e0c1f30, #177). The harness puts every applicable row into the
brief (`harness_bridge.py:435`), so nothing is withheld and nothing is lost by
retiring the agent. But `harness_bridge.py:428` already notes 40 rows is
~4.5k tokens per round trip. The day the brief is bounded, nothing on this
path will make the truncation speak.

## Done when
A bounded brief carries a count of what it left out, and the stage card
renders it in the same place `_off_today_line` renders the day-type
suspensions (`timeboxing_cards.py:342`).
```

- [ ] **Step 3: The docs ticket, per CLAUDE.md**

`tickets/test_suite_docs_after_legacy_retirement.md`, in the shape of `tickets/test_suite_docs_after_prune.md`: `tests/README.md`'s "What's out" section now lists 23 + 37 more files and why; the doubles list is unchanged; the two superseded root docs gain a line that `McpCalendarClient` is gone and `tmbx/calendar/` is the calendar port; `src/fateforger/agents/timeboxing/README.md` and `src/fateforger/core/README.md` describe the graphiti/constraint_mcp backends as the timeboxing agent's — say they are the tasks defaults memory's now. Assign it to a sonnet subagent as the last step of the PR, as CLAUDE.md requires.

- [ ] **Step 4: Update `tests/README.md` "What's out"**

Add a second paragraph after the six-file list: the legacy `TimeboxingFlowAgent` was retired on 2026-09-09 with the 34 modules only it reached; 60 test files went with it because their subject was that code (the list is in the retirement PR); two were rewired to the harness's readers instead. The rule that decided each: *if the subject is deleted code, the test goes; if the subject is live and the deleted module was only a fixture, the test is rewired.*

- [ ] **Step 5: The live drive — for Hugo, not for an agent**

This is the one gate the suite cannot stand in for; commit `dda88f4` records every unit test passing while the live bot went to legacy. Add this checklist to the PR body, unchecked, and do not check it yourself:

```
- [ ] /timebox in the timeboxing channel opens a session root with the working card as first reply
- [ ] A plain "plan tomorrow" in a normal channel: the receptionist hands off, and a session root appears in the timeboxing channel (or where I typed, if none is configured) -- no ":warning: Exception: Recipient not found"
- [ ] A second message in the open DM session continues it in the same thread
- [ ] Press Undo on a committed candidate: the calendar write reverses and the card says so
- [ ] Press a button on an old legacy stage card in history: it rewrites to the retired-flow sentence
```
The how-to is in the memory note "Driving a live Slack timeboxing session".

- [ ] **Step 6: Commit, push, open the PR**

```bash
git add tests/README.md docs/superpowers/specs/2026-09-08-test-suite-composability-design.md tickets/ README_CALENDAR_MCP.md GOOGLE_CALENDAR_MCP_GUIDE.md
git commit -m "docs: record the legacy-agent retirement and its two spec deviations

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push -u origin chore/retire-legacy-timeboxing-agent
```

Open the PR against `chore/prune-and-consolidate-tests` (it stacks on #396; retarget to `main` once #396 merges). The body carries: the problem (one paragraph), the measured numbers from Task 7 Step 3 (files and lines deleted, tests before/after, wall-clock, the coverage-diff verdict), the two spec deviations, the live-drive checklist from Step 5, and the standard footer `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Then dispatch the docs ticket to a sonnet subagent and fold its commit into the branch before requesting review.
