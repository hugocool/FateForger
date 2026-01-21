from fateforger.agents.timeboxing import stage_gating


def test_stage_prompts_do_not_reference_tool_calls():
    prompt = stage_gating.COLLECT_CONSTRAINTS_PROMPT.lower()
    assert "list-events" not in prompt
    assert "ticktick" not in prompt
    assert "use your tools" not in prompt

    prompt = stage_gating.CAPTURE_INPUTS_PROMPT.lower()
    assert "list-events" not in prompt
    assert "ticktick" not in prompt
    assert "use your tools" not in prompt
