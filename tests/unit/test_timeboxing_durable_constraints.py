import asyncio
import types

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
)
from fateforger.core.config import settings


@pytest.mark.asyncio
async def test_durable_constraint_prefetch_populates_session(monkeypatch):
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._durable_constraint_prefetch_tasks = {}
    agent._durable_constraint_prefetch_semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(settings, "notion_timeboxing_parent_page_id", "parent")

    done = asyncio.Event()

    async def _fake_fetch(_self, _session, *, stage: TimeboxingStage):
        if stage == TimeboxingStage.SKELETON:
            done.set()
        return [
            Constraint(
                user_id="u1",
                channel_id=None,
                thread_ts=None,
                name="No early meetings",
                description="Avoid meetings before 09:00.",
                necessity=ConstraintNecessity.MUST,
                status=ConstraintStatus.LOCKED,
                source=ConstraintSource.USER,
                scope=ConstraintScope.PROFILE,
            )
        ]

    agent._fetch_durable_constraints = types.MethodType(_fake_fetch, agent)
    agent._collect_constraints = types.MethodType(
        lambda _self, _session: asyncio.sleep(0, result=[]), agent
    )
    agent._sync_durable_constraints_to_store = types.MethodType(
        lambda _self, _session, *, constraints: asyncio.sleep(0), agent
    )

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-01-21",
    )

    agent._queue_durable_constraint_prefetch(session=session, reason="test")

    await asyncio.wait_for(done.wait(), timeout=1.0)
    while session.pending_durable_constraints:
        await asyncio.sleep(0)

    assert TimeboxingStage.COLLECT_CONSTRAINTS.value in session.durable_constraints_by_stage
    assert TimeboxingStage.SKELETON.value in session.durable_constraints_by_stage
    assert TimeboxingStage.COLLECT_CONSTRAINTS.value in session.durable_constraints_loaded_stages
    assert TimeboxingStage.SKELETON.value in session.durable_constraints_loaded_stages
    assert session.pending_durable_constraints is False


@pytest.mark.asyncio
async def test_collect_constraints_merges_durable_with_session():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    durable = Constraint(
        user_id="u1",
        channel_id=None,
        thread_ts=None,
        name="Deep work block",
        description="Reserve 2 hours for deep work.",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
    )
    local = Constraint(
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
        name="Gym",
        description="Gym at 18:00.",
        necessity=ConstraintNecessity.MUST,
        status=ConstraintStatus.PROPOSED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.SESSION,
    )

    class _Store:
        async def list_constraints(self, **_kwargs):
            return [local]

    agent._constraint_store = _Store()

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
    )
    session.durable_constraints_by_stage[TimeboxingStage.COLLECT_CONSTRAINTS.value] = [durable]

    combined = await agent._collect_constraints(session)

    assert durable in combined
    assert local in combined
    assert durable in session.active_constraints
    assert local in session.active_constraints
