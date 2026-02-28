"""Unit tests for fateforger.agents.timeboxing.patching.

Tests ``_extract_patch()`` (including fenced-JSON handling),
``_build_context()``, and ``_patcher_system_prompt_with_schema()``.
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from types import SimpleNamespace

import pytest

from fateforger.agents.timeboxing import patching as patching_module
from fateforger.agents.timeboxing.planning_policy import (
    PLANNING_POLICY_VERSION,
    SHARED_PLANNING_POLICY_PROMPT,
    STAGE4_REFINEMENT_PROMPT,
)
from fateforger.agents.timeboxing.patching import (
    _PATCHER_SYSTEM_PROMPT,
    _build_context,
    _extract_patch,
    _patcher_system_prompt_with_schema,
    TimeboxPatcher,
)
from fateforger.agents.timeboxing.tb_models import (
    ET,
    AfterPrev,
    FixedStart,
    TBEvent,
    TBPlan,
)
from fateforger.agents.timeboxing.tb_ops import AddEvents, TBPatch, UpdateEvent

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def simple_plan() -> TBPlan:
    """A minimal TBPlan for testing."""
    return TBPlan(
        date=date(2026, 2, 15),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Morning routine",
                t=ET.H,
                p=FixedStart(st=time(7, 0), dur=timedelta(minutes=30)),
            ),
            TBEvent(
                n="Deep work",
                t=ET.DW,
                p=AfterPrev(dur=timedelta(hours=2)),
            ),
        ],
    )


def _make_response(content: object) -> SimpleNamespace:
    """Build a fake AutoGen Response with the given content."""
    return SimpleNamespace(chat_message=SimpleNamespace(content=content))


# ── _extract_patch ────────────────────────────────────────────────────────


class TestExtractPatch:
    """Tests for ``_extract_patch()``."""

    def test_structured_tbpatch_object(self) -> None:
        """If content is already a TBPatch, return it directly."""
        patch = TBPatch(ops=[UpdateEvent(i=0, n="Renamed")])
        resp = _make_response(patch)
        assert _extract_patch(resp) is patch

    def test_raw_json_string(self) -> None:
        """Parse a clean JSON string into TBPatch."""
        raw = json.dumps({"ops": [{"op": "ue", "i": 0, "n": "Renamed"}]})
        resp = _make_response(raw)
        patch = _extract_patch(resp)
        assert len(patch.ops) == 1
        assert patch.ops[0].op == "ue"

    def test_fenced_json_string(self) -> None:
        """Parse JSON wrapped in markdown code fences."""
        raw = '```json\n{"ops": [{"op": "ue", "i": 1, "n": "Updated"}]}\n```'
        resp = _make_response(raw)
        patch = _extract_patch(resp)
        assert len(patch.ops) == 1
        assert patch.ops[0].n == "Updated"

    def test_fenced_json_no_language_tag(self) -> None:
        """Parse JSON wrapped in bare ``` fences (no language tag)."""
        raw = '```\n{"ops": [{"op": "re", "i": 0}]}\n```'
        resp = _make_response(raw)
        patch = _extract_patch(resp)
        assert patch.ops[0].op == "re"

    def test_fenced_json_multiline(self) -> None:
        """Parse a multi-line fenced JSON block."""
        raw = (
            "```json\n"
            "{\n"
            '  "ops": [\n'
            '    {"op": "ae", "events": [{"n": "Lunch", "t": "R", '
            '"p": {"a": "fs", "st": "12:00", "dur": "PT30M"}}]}\n'
            "  ]\n"
            "}\n"
            "```"
        )
        resp = _make_response(raw)
        patch = _extract_patch(resp)
        assert len(patch.ops) == 1
        assert patch.ops[0].op == "ae"

    def test_dict_content(self) -> None:
        """Parse a dict directly into TBPatch."""
        d = {"ops": [{"op": "ue", "i": 0, "n": "Renamed"}]}
        resp = _make_response(d)
        patch = _extract_patch(resp)
        assert patch.ops[0].n == "Renamed"

    def test_invalid_content_raises(self) -> None:
        """Raise ValueError for unparseable content."""
        resp = _make_response(42)
        with pytest.raises(ValueError, match="Could not extract TBPatch"):
            _extract_patch(resp)

    def test_invalid_json_string_raises(self) -> None:
        """Raise ValueError for a string that is not valid JSON."""
        resp = _make_response("this is not json")
        with pytest.raises(ValueError, match="TBPatch parse/validation failed"):
            _extract_patch(resp)

    def test_invalid_dict_raises_validation_details(self) -> None:
        """Raise ValueError with details for malformed dict payloads."""
        resp = _make_response({"ops": [{"op": "ue", "i": "bad-index"}]})
        with pytest.raises(ValueError, match="TBPatch validation failed from dict payload"):
            _extract_patch(resp)


# ── _build_context ────────────────────────────────────────────────────────


class TestBuildContext:
    """Tests for ``_build_context()``."""

    def test_contains_plan_json(self, simple_plan: TBPlan) -> None:
        """Context includes the plan as JSON."""
        ctx = _build_context(simple_plan, "add lunch", [], [])
        assert '"Morning routine"' in ctx
        assert '"Deep work"' in ctx

    def test_contains_user_message(self, simple_plan: TBPlan) -> None:
        """Context includes the user's instruction."""
        ctx = _build_context(simple_plan, "add a lunch break at noon", [], [])
        assert "add a lunch break at noon" in ctx

    def test_contains_produce_directive(self, simple_plan: TBPlan) -> None:
        """Context ends with the produce directive."""
        ctx = _build_context(simple_plan, "anything", [], [])
        assert "Produce the TBPatch JSON" in ctx


# ── _patcher_system_prompt_with_schema ────────────────────────────────────


class TestPatcherSystemPrompt:
    """Tests for ``_patcher_system_prompt_with_schema()``."""

    def test_includes_base_prompt(self) -> None:
        """The augmented prompt includes the original system prompt."""
        full = _patcher_system_prompt_with_schema()
        assert "timebox refinement assistant" in full

    def test_includes_json_schema(self) -> None:
        """The augmented prompt includes the TBPatch JSON schema."""
        full = _patcher_system_prompt_with_schema()
        assert "TBPatch JSON Schema" in full
        assert '"title": "TBPatch"' in full

    def test_includes_no_fences_instruction(self) -> None:
        """The augmented prompt tells the LLM not to use fences."""
        full = _patcher_system_prompt_with_schema()
        assert "no markdown fences" in full

    def test_includes_shared_planning_policy(self) -> None:
        """Patcher prompt should include the shared policy version + content."""
        full = _patcher_system_prompt_with_schema()
        assert PLANNING_POLICY_VERSION in full
        assert SHARED_PLANNING_POLICY_PROMPT.splitlines()[0] in full
        assert STAGE4_REFINEMENT_PROMPT.splitlines()[0] in full


@pytest.mark.asyncio
async def test_apply_patch_rejects_non_refine_stage(
    simple_plan: TBPlan,
) -> None:
    """Patcher API should hard-fail if called for a non-Refine stage."""
    patcher = TimeboxPatcher(model_client=object(), max_attempts=1)
    with pytest.raises(ValueError, match="only supports stage='Refine'"):
        await patcher.apply_patch(  # type: ignore[arg-type]
            stage="Skeleton",
            current=simple_plan,
            user_message="anything",
            constraints=[],
            actions=[],
        )


@pytest.mark.asyncio
async def test_apply_patch_retries_on_validator_failure(
    simple_plan: TBPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retry should include validator errors in the second patch attempt context."""
    contexts: list[str] = []
    attempts = {"validator": 0}
    raw_patch = json.dumps({"ops": [{"op": "ue", "i": 1, "n": "Deep work (updated)"}]})

    class _FakeAssistant:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs

        async def on_messages(self, messages: list[object], cancellation_token: object) -> object:
            _ = cancellation_token
            contexts.append(getattr(messages[0], "content", ""))
            return _make_response(raw_patch)

    async def _passthrough_timeout(
        label: str, awaitable: object, *, timeout_s: float
    ) -> object:
        _ = (label, timeout_s)
        return await awaitable  # type: ignore[misc]

    def _validator(_plan: TBPlan) -> None:
        attempts["validator"] += 1
        if attempts["validator"] == 1:
            raise ValueError("Overlap detected between events.")

    monkeypatch.setattr(patching_module, "AssistantAgent", _FakeAssistant)
    monkeypatch.setattr(patching_module, "with_timeout", _passthrough_timeout)
    patcher = TimeboxPatcher(model_client=object(), max_attempts=2)

    patched, patch = await patcher.apply_patch(
        stage="Refine",
        current=simple_plan,
        user_message="adjust deep work",
        constraints=[],
        actions=[],
        plan_validator=_validator,
    )

    assert patch.ops[0].op == "ue"
    assert patched.events[1].n == "Deep work (updated)"
    assert attempts["validator"] == 2
    assert len(contexts) == 2
    assert "Previous patch attempt failed." in contexts[1]
    assert "Overlap detected between events." in contexts[1]


@pytest.mark.asyncio
async def test_apply_patch_raises_after_max_attempts(
    simple_plan: TBPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patcher should raise a bounded error after exhausting retries."""
    raw_patch = json.dumps({"ops": [{"op": "ue", "i": 1, "n": "Deep work (updated)"}]})

    class _FakeAssistant:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs

        async def on_messages(self, messages: list[object], cancellation_token: object) -> object:
            _ = (messages, cancellation_token)
            return _make_response(raw_patch)

    async def _passthrough_timeout(
        label: str, awaitable: object, *, timeout_s: float
    ) -> object:
        _ = (label, timeout_s)
        return await awaitable  # type: ignore[misc]

    def _validator(_plan: TBPlan) -> None:
        raise ValueError("Still invalid: overlap remains.")

    monkeypatch.setattr(patching_module, "AssistantAgent", _FakeAssistant)
    monkeypatch.setattr(patching_module, "with_timeout", _passthrough_timeout)
    patcher = TimeboxPatcher(model_client=object(), max_attempts=2)

    with pytest.raises(ValueError, match="failed after 2 attempts"):
        await patcher.apply_patch(
            stage="Refine",
            current=simple_plan,
            user_message="adjust deep work",
            constraints=[],
            actions=[],
            plan_validator=_validator,
        )


@pytest.mark.asyncio
async def test_apply_patch_stops_on_non_retryable_provider_error(
    simple_plan: TBPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-retryable provider errors should fail fast instead of spending all retries."""
    attempts = {"calls": 0}

    class _PermissionDeniedError(Exception):
        status_code = 403

    class _FakeAssistant:
        def __init__(self, **kwargs: object) -> None:
            _ = kwargs

        async def on_messages(
            self, messages: list[object], cancellation_token: object
        ) -> object:
            _ = (messages, cancellation_token)
            attempts["calls"] += 1
            raise _PermissionDeniedError("forbidden")

    async def _passthrough_timeout(
        label: str, awaitable: object, *, timeout_s: float
    ) -> object:
        _ = (label, timeout_s)
        return await awaitable  # type: ignore[misc]

    monkeypatch.setattr(patching_module, "AssistantAgent", _FakeAssistant)
    monkeypatch.setattr(patching_module, "with_timeout", _passthrough_timeout)
    patcher = TimeboxPatcher(model_client=object(), max_attempts=5)

    with pytest.raises(ValueError, match="non-retryable error"):
        await patcher.apply_patch(
            stage="Refine",
            current=simple_plan,
            user_message="adjust deep work",
            constraints=[],
            actions=[],
        )
    assert attempts["calls"] == 1
