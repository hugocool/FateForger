from fateforger.agents.timeboxing.stage_gating import CAPTURE_INPUTS_PROMPT


def test_capture_inputs_prompt_prefers_block_scoping() -> None:
    """Pin that CaptureInputs defaults to block-based scoping, not time estimates."""
    prompt = CAPTURE_INPUTS_PROMPT.lower()
    assert "block_count" in prompt
    assert "durations are optional" in prompt
    assert "how long" not in prompt
    assert "lead summary with what is still missing" in prompt
