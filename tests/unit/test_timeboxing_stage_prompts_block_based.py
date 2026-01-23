"""Prompt coverage for block-based timeboxing guidance."""

from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.prompts import TIMEBOXING_SYSTEM_PROMPT
from fateforger.agents.timeboxing.stage_gating import CAPTURE_INPUTS_PROMPT


def test_capture_inputs_prompt_mentions_blocks() -> None:
    """Ensure CaptureInputs prompt prefers block allocations over durations."""
    assert "block allocation" in CAPTURE_INPUTS_PROMPT
    assert "durations are optional" in CAPTURE_INPUTS_PROMPT


def test_system_prompt_mentions_block_based_planning() -> None:
    """Ensure system prompt signals block-based planning as default."""
    assert "block-based scheduling" in TIMEBOXING_SYSTEM_PROMPT
