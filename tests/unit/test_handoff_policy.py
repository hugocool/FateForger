from __future__ import annotations

from fateforger.agents.shared.handoff_policy import (
    HandoffIntent,
    HandoffPolicy,
    HandoffRoute,
)


def test_handoff_policy_requires_explicit_target_and_confidence() -> None:
    policy = HandoffPolicy(allowed_targets={"tasks_agent"}, min_confidence=0.8)

    assert (
        policy.resolve(
            HandoffIntent(action="assist", target="tasks_agent", confidence=0.95)
        )
        == HandoffRoute.HANDOFF
    )
    assert (
        policy.resolve(
            HandoffIntent(action="assist", target="tasks_agent", confidence=0.4)
        )
        == HandoffRoute.STAY_CURRENT
    )
    assert (
        policy.resolve(HandoffIntent(action="assist", target=None, confidence=0.95))
        == HandoffRoute.STAY_CURRENT
    )
    assert (
        policy.resolve(
            HandoffIntent(action="provide_info", target="tasks_agent", confidence=1.0)
        )
        == HandoffRoute.STAY_CURRENT
    )
