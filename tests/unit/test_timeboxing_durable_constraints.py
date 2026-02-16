import asyncio
import types

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.nlu import ConstraintInterpretation
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from fateforger.agents.timeboxing.preferences import (
    ConstraintBase,
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


@pytest.mark.asyncio
async def test_profile_constraints_auto_upsert_to_durable_store(monkeypatch):
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    monkeypatch.setattr(settings, "notion_timeboxing_parent_page_id", "parent")

    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)
    agent._durable_constraint_semaphore = asyncio.Semaphore(1)
    agent._durable_constraint_task_keys = set()
    agent._constraint_extraction_tasks = {}
    agent._durable_constraint_prefetch_tasks = {}

    captured_adds: list[list[ConstraintBase]] = []
    captured_upserts: list[dict] = []
    prefetch_reasons: list[str] = []

    class _Store:
        async def add_constraints(self, **kwargs):
            captured_adds.append(list(kwargs["constraints"]))
            return []

    class _Client:
        async def upsert_constraint(self, *, record: dict, event: dict | None = None):
            captured_upserts.append({"record": record, "event": event})
            return {"uid": "tb_uid"}

    async def _fake_interpret(self, _session, *, text: str, is_initial: bool):
        _ = (text, is_initial)
        return ConstraintInterpretation(
            should_extract=True,
            scope="profile",
            constraints=[
                ConstraintBase(
                    name="No calls after 17:00",
                    description="Avoid meetings after 17:00.",
                    necessity=ConstraintNecessity.SHOULD,
                    scope=ConstraintScope.PROFILE,
                    status=ConstraintStatus.PROPOSED,
                    source=ConstraintSource.USER,
                    tags=["meetings"],
                )
            ],
        )

    async def _fake_collect(self, _session):
        return []

    async def _fake_ensure_store(self):
        return None

    agent._constraint_store = _Store()
    agent._ensure_constraint_store = types.MethodType(_fake_ensure_store, agent)
    agent._interpret_constraints = types.MethodType(_fake_interpret, agent)
    agent._collect_constraints = types.MethodType(_fake_collect, agent)
    agent._ensure_constraint_memory_client = types.MethodType(
        lambda _self: _Client(), agent
    )
    agent._queue_durable_constraint_prefetch = types.MethodType(
        lambda _self, *, session, reason: prefetch_reasons.append(reason), agent
    )

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", planned_date="2026-02-14")
    task = agent._queue_constraint_extraction(
        session=session,
        text="In general, no calls after 5pm.",
        reason="graphflow_turn",
        is_initial=False,
    )
    assert task is not None
    await task

    # Wait for the background durable upsert task to flush.
    for _ in range(50):
        if not agent._durable_constraint_task_keys:
            break
        await asyncio.sleep(0.01)

    assert captured_adds, "Expected local session constraints to be persisted."
    assert captured_upserts, "Expected durable Notion upsert to be attempted."
    upsert_record = captured_upserts[0]["record"]["constraint_record"]
    assert upsert_record["scope"] == "profile"
    assert TimeboxingStage.COLLECT_CONSTRAINTS.value in upsert_record["applies_stages"]
    assert "DW" in upsert_record["applies_event_types"]
    assert "post_upsert" in prefetch_reasons


@pytest.mark.asyncio
async def test_await_pending_durable_prefetch_waits_for_task():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", planned_date="2026-02-14")
    task_key = agent._durable_prefetch_key(session)
    slow_task = asyncio.create_task(asyncio.sleep(0.05))
    agent._durable_constraint_prefetch_tasks = {task_key: slow_task}

    await agent._await_pending_durable_constraint_prefetch(session, timeout_s=0.5)
    assert slow_task.done() is True
