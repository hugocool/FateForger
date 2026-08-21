"""Tests for timeboxing constants — timeout and limit sanity invariants.

These tests encode operational invariants derived from production observations:
- stage_gate_s observed p95 latency was ~46s in session 1773337741.092819.
  The timeout must exceed observed p95 with a safety margin.
- stage_gate_s must fit inside graph_turn_s with at least 30s to spare.
"""

import pytest

from fateforger.agents.timeboxing.constants import TIMEBOXING_TIMEOUTS


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
