from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.constants import TIMEBOXING_LIMITS
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintDayOfWeek,
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


@pytest.mark.asyncio
async def test_collect_constraints_logs_raw_and_applicable_active_counts() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    events: list[tuple[str, dict[str, object]]] = []
    agent._session_debug = lambda _session, event, **kwargs: events.append((event, kwargs))
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        committed=True,
        planned_date="2026-03-06",  # Friday
    )
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    applicable = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Friday planning",
        description="Works on Friday",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        days_of_week=[ConstraintDayOfWeek.FR],
        hints={"uid": "applicable"},
    )
    wrong_day = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Monday only",
        description="Only for Monday",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        days_of_week=[ConstraintDayOfWeek.MO],
        hints={"uid": "wrong-day"},
    )
    expired = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Expired window",
        description="No longer applicable",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.DATESPAN,
        start_date="2026-02-01",
        end_date="2026-02-10",
        hints={"uid": "expired"},
    )
    session.durable_constraints_by_stage = {
        TimeboxingStage.COLLECT_CONSTRAINTS.value: [applicable, wrong_day, expired]
    }

    active = await TimeboxingFlowAgent._collect_constraints(agent, session)

    assert [item.name for item in active] == ["Friday planning"]
    assert session.active_constraints_raw_count == 3
    assert session.active_constraints_applicable_count == 1
    snapshots = [payload for event, payload in events if event == "constraints_active_snapshot"]
    assert snapshots, "Expected constraints_active_snapshot debug event."
    assert snapshots[-1]["active_raw_count"] == 3
    assert snapshots[-1]["active_applicable_count"] == 1
    assert snapshots[-1]["active_filtered_out_count"] == 2


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
