"""Unit tests for fateforger.agents.timeboxing.patching.

Tests ``_extract_patch()`` (including fenced-JSON handling),
``_build_context()``, and ``_patcher_system_prompt_with_schema()``.
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fateforger.agents.timeboxing.patching import (
    _PATCHER_SYSTEM_PROMPT,
    _build_context,
    _extract_patch,
    _patcher_system_prompt_with_schema,
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
        with pytest.raises(ValueError, match="Could not extract TBPatch"):
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
