from fateforger.agents.timeboxing.constants import TIMEBOXING_TIMEOUTS


def test_stage_gate_timeout_budget_has_headroom() -> None:
    """Stage-gate LLM calls should have enough budget for real Slack runs."""
    assert TIMEBOXING_TIMEOUTS.stage_gate_s >= 35.0


def test_slow_turn_warning_threshold_is_set() -> None:
    """Slow-turn telemetry should have a deterministic threshold."""
    assert TIMEBOXING_TIMEOUTS.slow_turn_warn_s >= 30.0
