from fateforger.agents.timeboxing import stage_gating


def test_stage_prompts_do_not_reference_forbidden_tools():
    """Stage prompts must not reference MCP tools other than search_constraints."""
    for prompt_text in (
        stage_gating.COLLECT_CONSTRAINTS_PROMPT,
        stage_gating.CAPTURE_INPUTS_PROMPT,
    ):
        lower = prompt_text.lower()
        assert "list-events" not in lower
        assert "ticktick" not in lower
        assert "use your tools" not in lower


def test_collect_constraints_prompt_references_search_tool():
    """CollectConstraints prompt must guide the agent on using search_constraints."""
    prompt = stage_gating.COLLECT_CONSTRAINTS_PROMPT
    assert "search_constraints" in prompt
    # Must explain key filter fields.
    assert "text_query" in prompt
    assert "event_types" in prompt
    assert "statuses" in prompt
    assert "scopes" in prompt
    assert "necessities" in prompt
    assert "planned_date" in prompt
    # Must NOT tell the agent to avoid tools (old instruction).
    assert "you should not request tools" not in prompt


def test_capture_inputs_prompt_mentions_search_tool():
    """CaptureInputs prompt should mention search_constraints as optional."""
    prompt = stage_gating.CAPTURE_INPUTS_PROMPT
    assert "search_constraints" in prompt
    # Must NOT tell the agent the coordinator fetches in background (old instruction).
    assert "coordinator will fetch in background" not in prompt
