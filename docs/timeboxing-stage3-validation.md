# Timeboxing Stage 3 Validation

Stage 3 of the timeboxing flow takes the previously captured constraints and inputs and actually drafts a schedule (skeleton/planning stage). To validate this stage we plan to:

1. Run `pytest tests/unit/test_timeboxing_skeleton_fallback.py` and related prompt/context coverage tests to confirm the skeleton drafts remain stable when agents time out or receive minimal context.
2. Refine `tests/unit/test_timeboxing_prompt_rendering.py` and `tests/unit/test_timeboxing_stage_gate_json_context.py` once Stage 3 tooling changes arrive, so we explicitly assert the TOON-formatted JSON/blocks shipped to the stage agent.
3. Exercise the `TimeboxingFlowAgent` GraphFlow runner (`tests/unit/test_timeboxing_graphflow_state_machine.py`) with Stage 3 inputs so the stage gate’s `output_content_type` handling and background constraint extraction stay non-blocking.
4. Stress-test the real Slack/SlackBot integration (see `tests/e2e/test_slack_timeboxing_background_status.py`) to make sure Stage 3 commits its draft and posts the persistent preview.

Once these steps pass on CI, the stage-3 flow can be considered validated for this release window.
