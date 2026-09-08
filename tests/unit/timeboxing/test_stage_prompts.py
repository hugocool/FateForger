"""What the stage prompts must and must not say.

These assert the contracts a prompt carries, not its phrasing: that it scopes
work in blocks rather than asking for durations, that it defaults
deterministically before reaching for search, and that it never names a tool
the agent does not have. A prompt may be reworded freely as long as those
hold.
"""

from __future__ import annotations

from fateforger.agents.timeboxing import stage_gating
from fateforger.agents.timeboxing.stage_gating import CAPTURE_INPUTS_PROMPT


# ── CaptureInputs: blocks, not durations ────────────────────────────────────


def test_capture_inputs_prompt_prefers_block_scoping() -> None:
    """Pin that CaptureInputs defaults to block-based scoping, not time estimates."""
    prompt = CAPTURE_INPUTS_PROMPT.lower()
    assert "block_count" in prompt
    assert "durations are optional" in prompt
    assert "how long" not in prompt
    assert "lead summary with what is still missing" in prompt


# ── the tools a prompt may name ─────────────────────────────────────────────


def test_stage_prompts_do_not_reference_forbidden_tools():
    """Stage prompts must not reference forbidden/nonexistent external tools."""
    for prompt_text in (
        stage_gating.COLLECT_CONSTRAINTS_PROMPT,
        stage_gating.CAPTURE_INPUTS_PROMPT,
    ):
        lower = prompt_text.lower()
        assert "list-events" not in lower
        assert "ticktick" not in lower
        assert "use your tools" not in lower


def test_collect_constraints_prompt_is_deterministic_first_with_fallback_search():
    """CollectConstraints should be deterministic-first with fallback search guidance."""
    prompt = stage_gating.COLLECT_CONSTRAINTS_PROMPT
    assert "search_constraints" in prompt
    assert "deterministic-first defaulting" in prompt.lower()
    assert "injected durable constraints/defaults" in prompt.lower()
    assert "fallback" in prompt.lower()


def test_capture_inputs_prompt_mentions_search_tool():
    """CaptureInputs prompt should mention search_constraints as optional."""
    prompt = stage_gating.CAPTURE_INPUTS_PROMPT
    assert "search_constraints" in prompt
    # Must NOT tell the agent the coordinator fetches in background (old instruction).
    assert "coordinator will fetch in background" not in prompt
