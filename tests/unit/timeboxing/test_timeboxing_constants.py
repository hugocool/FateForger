"""The tunable constants of a timeboxing session, and the timeouts derived
from them.
"""

from __future__ import annotations

import pytest
from fateforger.agents.timeboxing.constants import TIMEBOXING_TIMEOUTS


# ── constants ─────────────────────────────────────────────────────────────────

class TestStageGateTimeout:
    """Timeout values must be grounded in observed runtime latency."""

    def test_stage_gate_timeout_exceeds_observed_p95_latency_with_margin(self) -> None:
        """stage_gate_s must be >= 60s.

        Production observation (2026-03-12, session 1773337741.092819):
        graph_turn_slow fired at elapsed_s=46.011 while gate was still running.
        The 35s default was too tight. 60s provides a safe margin over p95.
        """
        assert TIMEBOXING_TIMEOUTS.stage_gate_s >= 60.0, (
            f"stage_gate_s={TIMEBOXING_TIMEOUTS.stage_gate_s} is below the 60s "
            "minimum needed to survive observed p95 LLM latency (~46s). "
            "Raise it in constants.py."
        )

    def test_stage_gate_timeout_leaves_budget_inside_graph_turn(self) -> None:
        """stage_gate_s must leave at least 30s of headroom inside graph_turn_s.

        The graph turn does more than just run the stage gate (constraint loading,
        calendar prefetch, presenter formatting). The gate must not consume the
        full turn budget.
        """
        headroom = TIMEBOXING_TIMEOUTS.graph_turn_s - TIMEBOXING_TIMEOUTS.stage_gate_s
        assert headroom >= 30.0, (
            f"stage_gate_s={TIMEBOXING_TIMEOUTS.stage_gate_s} leaves only "
            f"{headroom:.1f}s inside graph_turn_s={TIMEBOXING_TIMEOUTS.graph_turn_s}. "
            "Need at least 30s of headroom."
        )


# ── timeouts ──────────────────────────────────────────────────────────────────

def test_stage_gate_timeout_budget_has_headroom() -> None:
    """Stage-gate LLM calls should have enough budget for real Slack runs."""
    assert TIMEBOXING_TIMEOUTS.stage_gate_s >= 35.0


def test_slow_turn_warning_threshold_is_set() -> None:
    """Slow-turn telemetry should have a deterministic threshold."""
    assert TIMEBOXING_TIMEOUTS.slow_turn_warn_s >= 30.0
