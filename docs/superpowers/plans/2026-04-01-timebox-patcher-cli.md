# Timebox Patcher CLI + Modular Prompt Architecture — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic `patching.py` prompt with a `PatcherContext` Pydantic model and build a REPL-style CLI that fetches from GCal, applies patches interactively, and pushes back.

**Architecture:** `PatcherContext` is the sole source of prompt text (system/user split, cacheable system prompt). `PatchConversation` holds multi-turn history across retries and across CLI turns. `TimeboxPatcher` is refactored to own the retry loop using both. The CLI is a thin stateful REPL: no LLM calls, no retry logic, no prompt strings.

**Tech Stack:** Python 3.11+, Pydantic v2, AutoGen AgentChat, existing `McpCalendarClient` + `CalendarSubmitter`, stdlib `argparse` + `cmd` for REPL.

**Spec:** `docs/superpowers/specs/2026-04-01-timebox-patcher-cli-design.md`
**Issue:** hugocool/FateForger#117

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/fateforger/agents/timeboxing/patcher_context.py` | Create | `ErrorFeedback`, `PatcherContext`, `PatchConversation` |
| `src/fateforger/agents/timeboxing/patching.py` | Modify | Delete `_PATCHER_SYSTEM_PROMPT`, `_build_context`; refactor `TimeboxPatcher` to use the new models |
| `src/fateforger/cli/__init__.py` | Create | Package marker |
| `src/fateforger/cli/patch.py` | Create | REPL CLI entrypoint: `load`, `patch`, `validate`, `submit` commands |
| `tests/unit/test_patcher_context.py` | Create | Unit tests for `PatcherContext`, `ErrorFeedback`, `PatchConversation` |
| `tests/unit/test_patching.py` | Modify | Update tests to match refactored `TimeboxPatcher` API |
| `tests/unit/test_patch_cli.py` | Create | Unit tests for CLI commands |

---

## Chunk 1: `PatcherContext`, `ErrorFeedback`, `PatchConversation`

### Task 1: `ErrorFeedback` model

**Files:**
- Create: `src/fateforger/agents/timeboxing/patcher_context.py`
- Create: `tests/unit/test_patcher_context.py`

- [ ] **Step 1: Write failing tests for `ErrorFeedback`**

In `tests/unit/test_patcher_context.py`:

```python
"""Tests for PatcherContext, ErrorFeedback, and PatchConversation."""
from __future__ import annotations

import json
from datetime import date, time, timedelta

import pytest

from fateforger.agents.timeboxing.patcher_context import ErrorFeedback
from fateforger.agents.timeboxing.tb_models import ET, FixedStart, TBEvent, TBPlan
from fateforger.agents.timeboxing.tb_ops import TBPatch, UpdateEvent


@pytest.fixture()
def simple_plan() -> TBPlan:
    return TBPlan(
        date=date(2026, 4, 1),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(n="Morning", t=ET.H, p=FixedStart(st=time(7, 0), dur=timedelta(minutes=30))),
        ],
    )


@pytest.fixture()
def simple_patch() -> TBPatch:
    return TBPatch(ops=[UpdateEvent(i=0, n="Morning routine")])


class TestErrorFeedback:
    def test_renders_error_message(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="Overlap detected.",
        )
        text = fb.render()
        assert "Overlap detected." in text

    def test_renders_original_plan_json(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="err",
        )
        text = fb.render()
        assert "Morning" in text  # original plan event name

    def test_renders_prior_patch_json(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="err",
        )
        text = fb.render()
        assert '"ue"' in text  # prior patch op code

    def test_renders_partial_result_when_present(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=simple_plan,
            error_message="err",
        )
        text = fb.render()
        assert "Partial result" in text

    def test_omits_partial_result_when_none(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="err",
        )
        text = fb.render()
        assert "Partial result" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_patcher_context.py -v
```

Expected: `ImportError` — `patcher_context` module does not exist yet.

- [ ] **Step 3: Create `patcher_context.py` with `ErrorFeedback`**

Create `src/fateforger/agents/timeboxing/patcher_context.py`:

```python
"""Modular prompt context for the timebox patcher.

Three components:
- ErrorFeedback: structured error state for retry turns
- PatcherContext: Pydantic model that renders system_prompt() + user_message_text()
- PatchConversation: caller-owned multi-turn history
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .tb_models import TBPlan
from .tb_ops import TBPatch

if TYPE_CHECKING:
    pass


class ErrorFeedback(BaseModel):
    """Structured error state injected into the user turn on retry.

    Gives the LLM all three states so it can decide whether to patch the
    patch or rewrite from the original plan.
    """

    model_config = ConfigDict(extra="forbid")

    original_plan: TBPlan
    prior_patch: TBPatch
    partial_result: TBPlan | None = None
    error_message: str

    def render(self) -> str:
        """Render the error feedback block as a prompt string."""
        original_json = self.original_plan.model_dump_json(indent=2)
        patch_json = self.prior_patch.model_dump_json(indent=2)
        lines = [
            "Previous patch attempt failed.",
            f"Error: {self.error_message}",
            "",
            "Original TBPlan (before this call):",
            f"```json\n{original_json}\n```",
            "",
            "Prior patch attempt:",
            f"```json\n{patch_json}\n```",
        ]
        if self.partial_result is not None:
            partial_json = self.partial_result.model_dump_json(indent=2)
            lines += [
                "",
                "Partial result (state after ops applied up to the error):",
                f"```json\n{partial_json}\n```",
            ]
        lines += [
            "",
            "You may patch the prior attempt OR produce a fresh patch against the original plan.",
            "Return a corrected TBPatch that resolves the error while preserving user intent.",
        ]
        return "\n".join(lines)


__all__ = ["ErrorFeedback"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_patcher_context.py::TestErrorFeedback -v
```

Expected: All `TestErrorFeedback` tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/patcher_context.py tests/unit/test_patcher_context.py
git commit -m "feat(patcher): ErrorFeedback model with render()"
```

---

### Task 2: `PatcherContext` model

**Files:**
- Modify: `src/fateforger/agents/timeboxing/patcher_context.py`
- Modify: `tests/unit/test_patcher_context.py`

- [ ] **Step 1: Write failing tests for `PatcherContext`**

Append to `tests/unit/test_patcher_context.py`:

```python
from fateforger.agents.timeboxing.patcher_context import PatcherContext
from fateforger.agents.timeboxing.planning_policy import (
    PLANNING_POLICY_VERSION,
    SHARED_PLANNING_POLICY_PROMPT,
    STAGE4_REFINEMENT_PROMPT,
)


class TestPatcherContext:
    def test_system_prompt_contains_role_preamble(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "timebox refinement assistant" in sp

    def test_system_prompt_contains_tbpatch_schema(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "TBPatch" in sp
        assert '"title"' in sp

    def test_system_prompt_contains_tbplan_schema(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "TBPlan" in sp

    def test_system_prompt_contains_stage_rules(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert STAGE4_REFINEMENT_PROMPT.splitlines()[0] in sp

    def test_system_prompt_contains_planning_policy(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert SHARED_PLANNING_POLICY_PROMPT.splitlines()[0] in sp

    def test_system_prompt_contains_output_instruction(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        sp = ctx.system_prompt()
        assert "Return ONLY the raw TBPatch JSON" in sp

    def test_user_message_contains_plan_json(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        um = ctx.user_message_text()
        assert "Morning" in um

    def test_user_message_contains_instruction(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch at noon", stage_rules=STAGE4_REFINEMENT_PROMPT)
        um = ctx.user_message_text()
        assert "add lunch at noon" in um

    def test_user_message_contains_memories(self, simple_plan):
        ctx = PatcherContext(
            plan=simple_plan,
            user_message="add lunch",
            stage_rules=STAGE4_REFINEMENT_PROMPT,
            memories=["I prefer 45-min lunch blocks."],
        )
        um = ctx.user_message_text()
        assert "I prefer 45-min lunch blocks." in um

    def test_user_message_no_error_feedback_by_default(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        um = ctx.user_message_text()
        assert "Previous patch attempt failed" not in um

    def test_user_message_includes_error_feedback_when_set(self, simple_plan, simple_patch):
        fb = ErrorFeedback(
            original_plan=simple_plan,
            prior_patch=simple_patch,
            partial_result=None,
            error_message="Overlap detected.",
        )
        ctx = PatcherContext(
            plan=simple_plan,
            user_message="add lunch",
            stage_rules=STAGE4_REFINEMENT_PROMPT,
            error_feedback=fb,
        )
        um = ctx.user_message_text()
        assert "Previous patch attempt failed" in um
        assert "Overlap detected." in um

    def test_system_prompt_is_stable_across_calls(self, simple_plan):
        ctx = PatcherContext(plan=simple_plan, user_message="add lunch", stage_rules=STAGE4_REFINEMENT_PROMPT)
        assert ctx.system_prompt() == ctx.system_prompt()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_patcher_context.py::TestPatcherContext -v
```

Expected: `ImportError` — `PatcherContext` not defined yet.

- [ ] **Step 3: Implement `PatcherContext`**

Add to `src/fateforger/agents/timeboxing/patcher_context.py` after the `ErrorFeedback` class:

```python
from .preferences import Constraint
from .planning_policy import SHARED_PLANNING_POLICY_PROMPT
from .toon_views import constraints_rows
from fateforger.llm.toon import toon_encode


_OP_REFERENCE = """\
Available patch operations (field ``op`` discriminator):
- ``ae`` (AddEvents): add one or more events. ``after`` = insert-after index (None → append).
- ``re`` (RemoveEvent): remove event at index ``i``.
- ``ue`` (UpdateEvent): merge partial changes onto event at index ``i``.
- ``me`` (MoveEvent): reorder event from index ``fr`` to ``to``.
- ``ra`` (ReplaceAll): replace entire event list (full rebuild only).

Time placement (field ``a`` on the event's ``p`` object):
- ``ap`` (AfterPrev): starts after previous event ends; needs ``dur`` (ISO 8601).
- ``bn`` (BeforeNext): ends when next event starts; needs ``dur``.
- ``fs`` (FixedStart): pinned start; needs ``st`` (HH:MM) and ``dur``.
- ``fw`` (FixedWindow): fixed start and end; needs ``st`` and ``et``.

Event types (``t``): M (meeting), C (commute), DW (deep work), SW (shallow work),
PR (plan & review), H (habit), R (regeneration), BU (buffer), BG (background).
"""

_ROLE_PREAMBLE = (
    "You are a timebox refinement assistant. You receive the current schedule "
    "as a TBPlan JSON, a user instruction, and optional constraints and memories.\n\n"
    "Your task: produce a single TBPatch JSON with the minimal set of typed domain "
    "operations that fulfils the user's request."
)

_OUTPUT_INSTRUCTION = (
    "Return ONLY the raw TBPatch JSON object — no markdown fences, no commentary."
)


class PatcherContext(BaseModel):
    """Prompt context for a single patcher invocation.

    Renders into two prompt roles:
    - ``system_prompt()``: cacheable per (stage_rules, schema version)
    - ``user_message_text()``: rebuilt per call with current plan + instruction + error state
    """

    model_config = ConfigDict(extra="forbid")

    plan: TBPlan
    user_message: str
    stage_rules: str
    constraints: list[Constraint] | None = None
    memories: list[str] | None = None
    error_feedback: ErrorFeedback | None = None

    def system_prompt(self) -> str:
        """Render the cacheable system prompt.

        Contains: role preamble, both JSON schemas, op reference,
        stage rules, shared planning policy, output instruction.
        """
        tbpatch_schema = json.dumps(TBPatch.model_json_schema(), indent=2)
        tbplan_schema = json.dumps(TBPlan.model_json_schema(), indent=2)
        sections = [
            _ROLE_PREAMBLE,
            "",
            "## TBPatch JSON Schema",
            f"```json\n{tbpatch_schema}\n```",
            "",
            "## TBPlan JSON Schema (input format)",
            f"```json\n{tbplan_schema}\n```",
            "",
            "## Operations Reference",
            _OP_REFERENCE,
            "",
            "## Stage Rules",
            self.stage_rules,
            "",
            "## Planning Policy",
            SHARED_PLANNING_POLICY_PROMPT,
            "",
            _OUTPUT_INSTRUCTION,
        ]
        return "\n".join(sections)

    def user_message_text(self) -> str:
        """Render the per-call user turn.

        Contains: current TBPlan JSON, user instruction,
        optional constraints, optional memories, optional error feedback.
        """
        plan_json = self.plan.model_dump_json(indent=2)
        parts: list[str] = [
            f"Current TBPlan:\n```json\n{plan_json}\n```",
            "",
            f"User request: {self.user_message}",
        ]

        if self.constraints:
            toon = toon_encode(
                name="constraints",
                rows=constraints_rows(self.constraints),
                fields=["name", "necessity", "scope", "status", "source", "description"],
            )
            parts += ["", toon]

        if self.memories:
            parts += ["", "Memories / preferences:"]
            parts.extend(f"- {m}" for m in self.memories)

        if self.error_feedback is not None:
            parts += ["", self.error_feedback.render()]

        parts += ["", "Produce the TBPatch JSON with minimal ops to fulfil the request."]
        return "\n".join(parts)


__all__ = ["ErrorFeedback", "PatcherContext"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_patcher_context.py::TestPatcherContext -v
```

Expected: All `TestPatcherContext` tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/patcher_context.py tests/unit/test_patcher_context.py
git commit -m "feat(patcher): PatcherContext with system_prompt() + user_message_text()"
```

---

### Task 3: `PatchConversation`

**Files:**
- Modify: `src/fateforger/agents/timeboxing/patcher_context.py`
- Modify: `tests/unit/test_patcher_context.py`

- [ ] **Step 1: Write failing tests for `PatchConversation`**

Append to `tests/unit/test_patcher_context.py`:

```python
from fateforger.agents.timeboxing.patcher_context import PatchConversation


class TestPatchConversation:
    def test_starts_empty(self):
        conv = PatchConversation()
        assert conv.turns == []

    def test_append_user_adds_turn(self):
        conv = PatchConversation()
        conv.append_user("add lunch")
        assert len(conv.turns) == 1
        assert conv.turns[0] == {"role": "user", "content": "add lunch"}

    def test_append_assistant_adds_turn(self):
        conv = PatchConversation()
        conv.append_assistant('{"ops":[]}')
        assert conv.turns[0]["role"] == "assistant"

    def test_reset_clears_turns(self):
        conv = PatchConversation()
        conv.append_user("x")
        conv.reset()
        assert conv.turns == []

    def test_max_turns_truncates_oldest_pairs(self):
        conv = PatchConversation(max_turns=2)
        for i in range(4):
            conv.append_user(f"msg {i}")
            conv.append_assistant(f"resp {i}")
        # max_turns=2 means keep last 2 user+assistant pairs = 4 turns
        assert len(conv.turns) <= 4
        assert conv.turns[-1]["content"] == "resp 3"

    def test_to_autogen_messages_format(self):
        conv = PatchConversation()
        conv.append_user("instruction")
        conv.append_assistant("patch json")
        msgs = conv.to_autogen_messages()
        assert msgs[0].source == "user"
        assert msgs[1].source == "assistant"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_patcher_context.py::TestPatchConversation -v
```

Expected: `ImportError` — `PatchConversation` not defined yet.

- [ ] **Step 3: Implement `PatchConversation`**

Add to `src/fateforger/agents/timeboxing/patcher_context.py` (after `PatcherContext`):

```python
from autogen_agentchat.messages import TextMessage


class PatchConversation:
    """Caller-owned multi-turn conversation history for the patcher.

    Holds alternating user/assistant turns. The CLI holds one instance
    per session; a future MCP wrapper holds one per calendar day.
    Retries within a single apply_patch call append to this same history.
    """

    def __init__(self, *, max_turns: int = 20) -> None:
        """
        Args:
            max_turns: Keep at most this many user+assistant *pairs*.
                Oldest pairs are dropped when the limit is exceeded.
        """
        self._max_turns = max_turns
        self.turns: list[dict[str, str]] = []

    def append_user(self, text: str) -> None:
        self.turns.append({"role": "user", "content": text})
        self._truncate()

    def append_assistant(self, text: str) -> None:
        self.turns.append({"role": "assistant", "content": text})
        self._truncate()

    def reset(self) -> None:
        """Clear history. Call after a ReplaceAll (ra) op."""
        self.turns = []

    def _truncate(self) -> None:
        """Drop oldest pairs if over max_turns pairs."""
        max_messages = self._max_turns * 2  # each pair = 2 messages
        if len(self.turns) > max_messages:
            self.turns = self.turns[-max_messages:]

    def to_autogen_messages(self) -> list[TextMessage]:
        """Convert turns to AutoGen TextMessage list for agent.on_messages()."""
        return [
            TextMessage(content=t["content"], source=t["role"])
            for t in self.turns
        ]
```

Update `__all__`:
```python
__all__ = ["ErrorFeedback", "PatcherContext", "PatchConversation"]
```

- [ ] **Step 4: Run all patcher_context tests**

```bash
pytest tests/unit/test_patcher_context.py -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/patcher_context.py tests/unit/test_patcher_context.py
git commit -m "feat(patcher): PatchConversation with multi-turn history + truncation"
```

---

## Chunk 2: Refactor `TimeboxPatcher`

### Task 4: Refactor `TimeboxPatcher` to use `PatcherContext` + `PatchConversation`

**Files:**
- Modify: `src/fateforger/agents/timeboxing/patching.py`
- Modify: `tests/unit/test_patching.py`

Key changes:
- Delete `_PATCHER_SYSTEM_PROMPT`, `_build_context` (replaced by `PatcherContext`)
- `apply_patch` accepts optional `conversation: PatchConversation | None` and `stage_rules: str | None`
- Retry loop becomes multi-turn: failed attempt is appended to conversation as assistant turn; next attempt appends a new user turn with `ErrorFeedback`
- `partial_result` is tracked: when `apply_tb_ops` raises mid-patch, capture how far it got

- [ ] **Step 1: Write failing tests for refactored `TimeboxPatcher`**

Append new tests to `tests/unit/test_patching.py`:

```python
from fateforger.agents.timeboxing.patcher_context import PatchConversation
from fateforger.agents.timeboxing.planning_policy import STAGE4_REFINEMENT_PROMPT


@pytest.mark.asyncio
async def test_apply_patch_uses_patcher_context_system_prompt(
    simple_plan: TBPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TimeboxPatcher should use PatcherContext.system_prompt() as agent system_message."""
    captured: list[str] = []
    raw_patch = json.dumps({"ops": [{"op": "ue", "i": 0, "n": "Updated"}]})

    class _FakeAssistant:
        def __init__(self, *, name, model_client, system_message, **kwargs):
            captured.append(system_message)

        async def on_messages(self, messages, cancellation_token):
            return _make_response(raw_patch)

    async def _passthrough_timeout(label, awaitable, *, timeout_s):
        return await awaitable

    monkeypatch.setattr(patching_module, "AssistantAgent", _FakeAssistant)
    monkeypatch.setattr(patching_module, "with_timeout", _passthrough_timeout)

    patcher = TimeboxPatcher(model_client=object(), max_attempts=1)
    await patcher.apply_patch(
        stage="Refine",
        current=simple_plan,
        user_message="update event",
        stage_rules=STAGE4_REFINEMENT_PROMPT,
    )

    assert len(captured) == 1
    assert "timebox refinement assistant" in captured[0]
    assert "TBPatch" in captured[0]


@pytest.mark.asyncio
async def test_apply_patch_appends_to_conversation(
    simple_plan: TBPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful patch should append user + assistant turns to conversation."""
    raw_patch = json.dumps({"ops": [{"op": "ue", "i": 0, "n": "Updated"}]})

    class _FakeAssistant:
        def __init__(self, **kwargs): pass

        async def on_messages(self, messages, cancellation_token):
            return _make_response(raw_patch)

    async def _passthrough_timeout(label, awaitable, *, timeout_s):
        return await awaitable

    monkeypatch.setattr(patching_module, "AssistantAgent", _FakeAssistant)
    monkeypatch.setattr(patching_module, "with_timeout", _passthrough_timeout)

    conv = PatchConversation()
    patcher = TimeboxPatcher(model_client=object(), max_attempts=1)
    await patcher.apply_patch(
        stage="Refine",
        current=simple_plan,
        user_message="update event",
        stage_rules=STAGE4_REFINEMENT_PROMPT,
        conversation=conv,
    )

    assert len(conv.turns) == 2
    assert conv.turns[0]["role"] == "user"
    assert conv.turns[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_retry_appends_error_feedback_as_new_user_turn(
    simple_plan: TBPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On retry, user turn should contain ErrorFeedback block."""
    user_messages_seen: list[str] = []
    raw_patch = json.dumps({"ops": [{"op": "ue", "i": 0, "n": "Updated"}]})
    call_count = {"n": 0}

    class _FakeAssistant:
        def __init__(self, **kwargs): pass

        async def on_messages(self, messages, cancellation_token):
            user_messages_seen.extend(
                m.content for m in messages if getattr(m, "source", None) == "user"
            )
            return _make_response(raw_patch)

    async def _passthrough_timeout(label, awaitable, *, timeout_s):
        return await awaitable

    def _validator(plan):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("Overlap detected between events.")

    monkeypatch.setattr(patching_module, "AssistantAgent", _FakeAssistant)
    monkeypatch.setattr(patching_module, "with_timeout", _passthrough_timeout)

    patcher = TimeboxPatcher(model_client=object(), max_attempts=2)
    await patcher.apply_patch(
        stage="Refine",
        current=simple_plan,
        user_message="update event",
        stage_rules=STAGE4_REFINEMENT_PROMPT,
        plan_validator=_validator,
    )

    # Second user message should contain error feedback
    assert len(user_messages_seen) >= 2
    assert "Previous patch attempt failed" in user_messages_seen[-1]
    assert "Overlap detected" in user_messages_seen[-1]
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
pytest tests/unit/test_patching.py::test_apply_patch_uses_patcher_context_system_prompt \
       tests/unit/test_patching.py::test_apply_patch_appends_to_conversation \
       tests/unit/test_patching.py::test_retry_appends_error_feedback_as_new_user_turn -v
```

Expected: `TypeError` or assertion failures — `apply_patch` doesn't accept `stage_rules` or `conversation` yet.

- [ ] **Step 3: Refactor `TimeboxPatcher.apply_patch`**

In `src/fateforger/agents/timeboxing/patching.py`:

1. Add imports at the top:
```python
from .patcher_context import ErrorFeedback, PatchConversation, PatcherContext
from .planning_policy import STAGE4_REFINEMENT_PROMPT
```

2. Delete `_PATCHER_SYSTEM_PROMPT` constant and `_build_context` function entirely.

3. Replace `_patcher_system_prompt_with_schema` with a simple delegation:
```python
def _patcher_system_prompt_with_schema(stage_rules: str = STAGE4_REFINEMENT_PROMPT) -> str:
    """Build system prompt via PatcherContext (used only for backward compat tests)."""
    from .tb_models import TBPlan as _TBPlan
    import datetime
    dummy = _TBPlan(date=datetime.date.today(), events=[])
    return PatcherContext(
        plan=dummy, user_message="", stage_rules=stage_rules
    ).system_prompt()
```

4. Update `apply_patch` signature:
```python
async def apply_patch(
    self,
    *,
    stage: Literal["Refine"],
    current: TBPlan,
    user_message: str,
    constraints: Iterable[Constraint] | None = None,
    actions: Iterable[TimeboxAction] | None = None,
    plan_validator: Callable[[TBPlan], Any] | None = None,
    stage_rules: str | None = None,
    conversation: PatchConversation | None = None,
    memories: list[str] | None = None,
) -> tuple[TBPlan, TBPatch]:
```

5. Replace the retry loop body:

```python
constraints_list = list(constraints or [])
actions_list = list(actions or [])
_stage_rules = stage_rules or STAGE4_REFINEMENT_PROMPT
request_id = f"patch-{int(time.time() * 1000)}"

original_plan = current  # snapshot before this call
error_feedback: ErrorFeedback | None = None
last_assistant_text: str | None = None
last_error: Exception | None = None
last_retryable = True

for attempt in range(1, self._max_attempts + 1):
    ctx = PatcherContext(
        plan=current,
        user_message=user_message,
        stage_rules=_stage_rules,
        constraints=constraints_list or None,
        memories=memories,
        error_feedback=error_feedback,
    )
    agent = AssistantAgent(
        name="TimeboxPatcherAgent",
        model_client=self._model_client,
        system_message=ctx.system_prompt(),
        reflect_on_tool_use=False,
    )
    user_text = ctx.user_message_text()
    # Build messages: prior conversation turns + new user message
    prior_messages = conversation.to_autogen_messages() if conversation else []
    new_user_msg = TextMessage(content=user_text, source="user")
    messages_for_agent = prior_messages + [new_user_msg]

    logger.debug(
        "timebox_patcher request_id=%s attempt=%s/%s",
        request_id, attempt, self._max_attempts,
    )

    try:
        response = await with_timeout(
            "timeboxing:patcher",
            agent.on_messages(messages_for_agent, CancellationToken()),
            timeout_s=TIMEBOXING_TIMEOUTS.skeleton_draft_s,
        )
        raw_content = getattr(getattr(response, "chat_message", None), "content", None)
        last_assistant_text = raw_content if isinstance(raw_content, str) else str(raw_content)

        patch = _extract_patch(response)
        partial: TBPlan | None = None
        try:
            patched = apply_tb_ops(current, patch)
        except Exception as apply_exc:
            partial = None  # apply_tb_ops is atomic per op; no partial state exposed
            raise apply_exc

        if plan_validator is not None:
            plan_validator(patched)

        # Success — record turns in conversation
        if conversation is not None:
            conversation.append_user(user_text)
            conversation.append_assistant(last_assistant_text or "")
            # Reset conversation on full rebuild
            if any(op.op == "ra" for op in patch.ops):
                conversation.reset()

        logger.info(
            "timebox_patcher request_id=%s success attempt=%s/%s ops=%s",
            request_id, attempt, self._max_attempts, len(patch.ops),
        )
        return patched, patch

    except Exception as exc:
        last_error = exc
        retryable = _is_retryable_patch_error(exc)
        last_retryable = retryable
        error_msg = _build_retry_feedback(error=exc)
        error_feedback = ErrorFeedback(
            original_plan=original_plan,
            prior_patch=patch if "patch" in dir() else TBPatch(ops=[]),  # guard
            partial_result=partial if "partial" in dir() else None,
            error_message=error_msg,
        )
        # Append failed turn to conversation before retry
        if conversation is not None and last_assistant_text:
            conversation.append_user(user_text)
            conversation.append_assistant(last_assistant_text)
        logger.warning(
            "timebox_patcher request_id=%s failed attempt=%s/%s retryable=%s",
            request_id, attempt, self._max_attempts, retryable,
        )
        if not retryable:
            break
        continue
```

**Note on partial result:** `apply_tb_ops` is currently atomic (applies all ops or raises). The `partial_result` field is included for future use when op-level partial tracking is added. For now it is always `None` on error.

- [ ] **Step 4: Run the full patching test suite**

```bash
pytest tests/unit/test_patching.py -v
```

Expected: All tests pass (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/agents/timeboxing/patching.py tests/unit/test_patching.py
git commit -m "refactor(patcher): TimeboxPatcher uses PatcherContext + PatchConversation for multi-turn retries"
```

---

## Chunk 3: CLI

### Task 5: REPL CLI — `fateforger patch`

**Requires:** Task 4 complete (adds `stage_rules` + `conversation` params to `apply_patch`).

**Files:**
- Create: `src/fateforger/cli/__init__.py`
- Create: `src/fateforger/cli/patch.py`
- Create: `tests/unit/test_patch_cli.py`

The CLI is a stateful REPL session:

- Session state: current `TBPlan | None`, `remote_plan: TBPlan | None`, `event_id_map: dict[str, str]`, `event_ids_by_index: list[str]`, `PatchConversation`
- The `load` command fetches from GCal using `gcal_response_to_tb_plan_with_identity` and stores all three (plan + remote identity data) on the session — `submit` needs them.
- Commands: `load`, `patch`, `validate`, `submit`, `show`, `help`, `quit`
- Uses stdlib `cmd.Cmd` for the REPL loop (readline support, help built-in)
- Async operations run via `asyncio.run()` per command (no persistent event loop needed)

- [ ] **Step 1: Write failing CLI tests**

Create `tests/unit/test_patch_cli.py`:

```python
"""Unit tests for the timebox patcher CLI session."""
from __future__ import annotations

import asyncio
import json
from datetime import date, time, timedelta

import pytest

from fateforger.agents.timeboxing.tb_models import ET, FixedStart, TBEvent, TBPlan
from fateforger.cli.patch import PatchSession


@pytest.fixture()
def simple_plan() -> TBPlan:
    return TBPlan(
        date=date(2026, 4, 1),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(n="Morning", t=ET.H, p=FixedStart(st=time(7, 0), dur=timedelta(minutes=30))),
        ],
    )


class TestPatchSession:
    def test_starts_with_no_plan(self):
        session = PatchSession()
        assert session.plan is None

    def test_load_sets_plan(self, simple_plan):
        session = PatchSession()
        session._set_plan(simple_plan)
        assert session.plan == simple_plan

    def test_validate_returns_resolved_times(self, simple_plan):
        session = PatchSession()
        session._set_plan(simple_plan)
        resolved = session._validate()
        assert resolved is not None
        assert len(resolved) == 1
        assert resolved[0]["n"] == "Morning"

    def test_validate_raises_without_plan(self):
        session = PatchSession()
        with pytest.raises(RuntimeError, match="No plan loaded"):
            session._validate()

    def test_show_returns_plan_json(self, simple_plan):
        session = PatchSession()
        session._set_plan(simple_plan)
        output = session._show()
        data = json.loads(output)
        assert data["events"][0]["n"] == "Morning"

    def test_patch_requires_plan(self):
        session = PatchSession()
        with pytest.raises(RuntimeError, match="No plan loaded"):
            asyncio.run(session._apply_patch("add lunch"))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_patch_cli.py -v
```

Expected: `ModuleNotFoundError` — `fateforger.cli.patch` does not exist yet.

- [ ] **Step 3: Create CLI package and `PatchSession`**

Create `src/fateforger/cli/__init__.py` (empty):
```python
```

Create `src/fateforger/cli/patch.py`:

```python
"""Timebox patcher REPL CLI.

Usage:
    python -m fateforger.cli.patch [--date YYYY-MM-DD] [--calendar-id ID]

Session commands:
    load [date]     Fetch TBPlan from GCal for date (default: today)
    show            Print current plan as JSON
    patch <text>    Apply a patch instruction (prompts for confirm)
    validate        Validate current plan (resolve times + overlap check)
    submit          Push current plan to GCal (prompts for confirm)
    reset           Clear conversation history
    help            Show this help
    quit / exit     Exit the session
"""

from __future__ import annotations

import asyncio
import cmd
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fateforger.agents.timeboxing.patcher_context import PatchConversation
from fateforger.agents.timeboxing.patching import TimeboxPatcher
from fateforger.agents.timeboxing.planning_policy import STAGE4_REFINEMENT_PROMPT
from fateforger.agents.timeboxing.tb_models import TBPlan


class PatchSession:
    """Holds in-session state: current TBPlan, remote identity data, and PatchConversation."""

    def __init__(self) -> None:
        self.plan: TBPlan | None = None
        self._remote_plan: TBPlan | None = None
        self._event_id_map: dict[str, str] = {}
        self._event_ids_by_index: list[str] = []
        self.conversation = PatchConversation()
        self._patcher: TimeboxPatcher | None = None

    def _set_plan(self, plan: TBPlan) -> None:
        self.plan = plan

    def _require_plan(self) -> TBPlan:
        if self.plan is None:
            raise RuntimeError("No plan loaded. Run 'load' first.")
        return self.plan

    def _validate(self) -> list[dict]:
        """Resolve times and check for overlaps. Returns resolved event list."""
        plan = self._require_plan()
        return plan.resolve_times(validate_non_overlap=True)

    def _show(self) -> str:
        """Return current plan as pretty JSON."""
        plan = self._require_plan()
        return plan.model_dump_json(indent=2)

    def _get_patcher(self) -> TimeboxPatcher:
        if self._patcher is None:
            self._patcher = TimeboxPatcher()
        return self._patcher

    async def _load_from_gcal(self, *, calendar_id: str, day: date, tz: ZoneInfo) -> TBPlan:
        """Fetch events from GCal MCP, convert to TBPlan, and store remote identity data.

        Stores remote_plan, event_id_map, and event_ids_by_index on self so
        that _submit_to_gcal can pass them to CalendarSubmitter.submit_plan().
        """
        from fateforger.agents.timeboxing.mcp_clients import McpCalendarClient
        from fateforger.agents.timeboxing.sync_engine import gcal_response_to_tb_plan_with_identity
        from fateforger.core.config import settings

        server_url = settings.mcp_calendar_server_url
        client = McpCalendarClient(server_url=server_url)
        try:
            snapshot = await client.list_day_snapshot(
                calendar_id=calendar_id, day=day, tz=tz
            )
        finally:
            await client.close()
        plan, event_id_map, event_ids_by_index = gcal_response_to_tb_plan_with_identity(
            snapshot.response,
            plan_date=day,
            tz_name=tz.key,
        )
        self._remote_plan = plan
        self._event_id_map = event_id_map
        self._event_ids_by_index = event_ids_by_index
        return plan

    async def _apply_patch(self, instruction: str) -> tuple[TBPlan, TBPatch]:
        """Apply a patch instruction. Returns (new_plan, patch)."""
        plan = self._require_plan()
        patcher = self._get_patcher()
        return await patcher.apply_patch(
            stage="Refine",
            current=plan,
            user_message=instruction,
            stage_rules=STAGE4_REFINEMENT_PROMPT,
            conversation=self.conversation,
        )

    async def _submit_to_gcal(self, *, calendar_id: str) -> None:
        """Push current plan to GCal via CalendarSubmitter.

        Requires that _load_from_gcal was called first (sets remote identity data).
        """
        from fateforger.agents.timeboxing.submitter import CalendarSubmitter

        plan = self._require_plan()
        if self._remote_plan is None:
            raise RuntimeError("No remote plan available. Run 'load' first.")
        submitter = CalendarSubmitter()
        await submitter.submit_plan(
            plan,
            remote=self._remote_plan,
            event_id_map=self._event_id_map,
            remote_event_ids_by_index=self._event_ids_by_index,
            calendar_id=calendar_id,
        )


class PatchRepl(cmd.Cmd):
    """Interactive REPL for the timebox patcher."""

    intro = (
        "Timebox Patcher — type 'help' for commands, 'quit' to exit.\n"
        "Start with: load [YYYY-MM-DD]\n"
    )
    prompt = "patch> "

    def __init__(self, *, calendar_id: str, tz: str = "Europe/Amsterdam") -> None:
        super().__init__()
        self._session = PatchSession()
        self._calendar_id = calendar_id
        self._tz = ZoneInfo(tz)

    # ── Commands ──────────────────────────────────────────────────────────

    def do_load(self, arg: str) -> None:
        """load [YYYY-MM-DD]  — Fetch TBPlan from GCal (default: today)."""
        raw = arg.strip()
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date() if raw else date.today()
        except ValueError:
            print(f"Invalid date: {raw!r}. Use YYYY-MM-DD.")
            return
        print(f"Fetching plan for {day} from calendar {self._calendar_id} …")
        try:
            plan = asyncio.run(
                self._session._load_from_gcal(
                    calendar_id=self._calendar_id, day=day, tz=self._tz
                )
            )
            self._session._set_plan(plan)
            print(f"Loaded {len(plan.events)} events.")
            self._print_plan_summary(plan)
        except Exception as exc:
            print(f"Error: {exc}")

    def do_show(self, _arg: str) -> None:
        """show  — Print current plan as JSON."""
        try:
            print(self._session._show())
        except RuntimeError as exc:
            print(f"Error: {exc}")

    def do_patch(self, arg: str) -> None:
        """patch <instruction>  — Apply a patch instruction."""
        instruction = arg.strip()
        if not instruction:
            print("Usage: patch <instruction>")
            return
        try:
            print(f"Patching: {instruction!r} …")
            new_plan, patch = asyncio.run(self._session._apply_patch(instruction))
            print(f"Patch applied ({len(patch.ops)} ops). Preview:")
            self._print_plan_summary(new_plan)
            confirm = input("Apply? [y/N] ").strip().lower()
            if confirm == "y":
                self._session._set_plan(new_plan)
                print("Plan updated.")
            else:
                print("Discarded.")
        except RuntimeError as exc:
            print(f"Error: {exc}")
        except Exception as exc:
            print(f"Patch failed: {exc}")

    def do_validate(self, _arg: str) -> None:
        """validate  — Validate current plan (resolve times, check overlaps)."""
        try:
            resolved = self._session._validate()
            print(f"Valid — {len(resolved)} events:")
            for r in resolved:
                st = r.get("start_time", "?")
                et = r.get("end_time", "?")
                print(f"  {st}–{et}  [{r['t']}] {r['n']}")
        except ValueError as exc:
            print(f"Validation error: {exc}")
        except RuntimeError as exc:
            print(f"Error: {exc}")

    def do_submit(self, _arg: str) -> None:
        """submit  — Push current plan to GCal (prompts for confirm)."""
        try:
            self._session._require_plan()
        except RuntimeError as exc:
            print(f"Error: {exc}")
            return
        confirm = input(f"Submit to calendar {self._calendar_id!r}? [y/N] ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
        try:
            asyncio.run(
                self._session._submit_to_gcal(calendar_id=self._calendar_id)
            )
            print("Submitted.")
        except Exception as exc:
            print(f"Submit failed: {exc}")

    def do_reset(self, _arg: str) -> None:
        """reset  — Clear conversation history."""
        self._session.conversation.reset()
        print("Conversation history cleared.")

    def do_quit(self, _arg: str) -> bool:
        """quit  — Exit the REPL."""
        print("Bye.")
        return True

    def do_exit(self, arg: str) -> bool:
        """exit  — Exit the REPL."""
        return self.do_quit(arg)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _print_plan_summary(self, plan: TBPlan) -> None:
        try:
            resolved = plan.resolve_times(validate_non_overlap=False)
            for r in resolved:
                st = r.get("start_time", "?")
                et = r.get("end_time", "?")
                print(f"  {st}–{et}  [{r['t']}] {r['n']}")
        except Exception:
            print(f"  ({len(plan.events)} events, unable to resolve times)")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Timebox Patcher REPL")
    parser.add_argument("--calendar-id", default=os.getenv("TIMEBOX_CALENDAR_ID", "primary"))
    parser.add_argument("--tz", default=os.getenv("TIMEBOX_TZ", "Europe/Amsterdam"))
    parser.add_argument("--date", help="Preload plan for this date (YYYY-MM-DD)")
    args = parser.parse_args()

    repl = PatchRepl(calendar_id=args.calendar_id, tz=args.tz)
    if args.date:
        repl.do_load(args.date)
    repl.cmdloop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run CLI tests**

```bash
pytest tests/unit/test_patch_cli.py -v
```

Expected: All tests pass.

- [ ] **Step 5: Register CLI script in `pyproject.toml`**

Add to `[project.scripts]` section:
```toml
patch = "fateforger.cli.patch:main"
```

- [ ] **Step 6: Run the full test suite to check nothing is broken**

```bash
pytest tests/unit/ -v --tb=short
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/fateforger/cli/ tests/unit/test_patch_cli.py pyproject.toml
git commit -m "feat(cli): fateforger patch REPL — load/patch/validate/submit commands"
```

---

## Final check

- [ ] Run full test suite one more time:

```bash
pytest tests/unit/ -v
```

Expected: All tests pass, no regressions.
