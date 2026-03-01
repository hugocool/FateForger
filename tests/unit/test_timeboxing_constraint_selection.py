from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.constants import TIMEBOXING_LIMITS
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


def _constraint(
    *,
    name: str,
    uid: str,
    necessity: ConstraintNecessity = ConstraintNecessity.SHOULD,
) -> Constraint:
    return Constraint(
        name=name,
        description=name,
        necessity=necessity,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        hints={"uid": uid},
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
    )


@pytest.mark.asyncio
async def test_collect_constraints_uses_relevant_durable_stage_sets() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.REFINE
    session.durable_constraints_by_stage = {
        TimeboxingStage.COLLECT_CONSTRAINTS.value: [_constraint(name="collect", uid="u-collect")],
        TimeboxingStage.SKELETON.value: [_constraint(name="skeleton", uid="u-skeleton")],
        TimeboxingStage.REFINE.value: [_constraint(name="refine", uid="u-refine")],
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert {item.name for item in active} == {"collect", "refine"}


def test_select_constraints_for_refine_patcher_caps_and_preserves_must() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.REFINE
    constraints = [
        _constraint(
            name=f"must-{idx}",
            uid=f"must-{idx}",
            necessity=ConstraintNecessity.MUST,
        )
        for idx in range(2)
    ] + [
        _constraint(name=f"should-{idx}", uid=f"should-{idx}")
        for idx in range(TIMEBOXING_LIMITS.refine_patcher_constraint_limit + 20)
    ]

    selected = TimeboxingFlowAgent._select_constraints_for_refine_patcher(
        agent,
        session=session,
        constraints=constraints,
    )

    assert len(selected) == TIMEBOXING_LIMITS.refine_patcher_constraint_limit
    assert {"must-0", "must-1"}.issubset({item.name for item in selected})
