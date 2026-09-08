from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import _constraint_priority
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)


def _constraint(name: str, necessity: ConstraintNecessity) -> Constraint:
    return Constraint(
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
        name=name,
        description=f"{name} description",
        necessity=necessity,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
    )


def test_constraint_priority_orders_must_should_prefer() -> None:
    constraints = [
        _constraint("C prefer", ConstraintNecessity.PREFER),
        _constraint("A must", ConstraintNecessity.MUST),
        _constraint("B should", ConstraintNecessity.SHOULD),
    ]

    ranked = sorted(constraints, key=_constraint_priority)
    assert [constraint.necessity for constraint in ranked] == [
        ConstraintNecessity.MUST,
        ConstraintNecessity.SHOULD,
        ConstraintNecessity.PREFER,
    ]
