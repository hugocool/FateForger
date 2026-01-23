import asyncio
import types

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.nlu import ConstraintInterpretation
from fateforger.agents.timeboxing.preferences import (
    ConstraintBase,
    ConstraintNecessity,
    ConstraintScope,
)


@pytest.mark.asyncio
async def test_queue_constraint_extraction_runs_in_background():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_extraction_tasks = {}
    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)

    async def _fake_interpret(_self, _session, *, text: str, is_initial: bool):
        assert text
        assert is_initial is False
        return ConstraintInterpretation(
            should_extract=True,
            scope="session",
            constraints=[
                ConstraintBase(
                    name="Deep work mornings",
                    description="Do deep work in the mornings.",
                    necessity=ConstraintNecessity.SHOULD,
                    scope=ConstraintScope.SESSION,
                )
            ],
        )

    class _Store:
        async def add_constraints(self, **_kwargs):
            return []

    async def _fake_collect_constraints(_session: Session):
        return []

    agent._interpret_constraints = types.MethodType(_fake_interpret, agent)  # type: ignore[assignment]
    async def _noop_store() -> None:
        return None

    agent._ensure_constraint_store = _noop_store  # type: ignore[assignment]
    agent._constraint_store = _Store()
    agent._collect_constraints = _fake_collect_constraints  # type: ignore[assignment]

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
    )

    task = agent._queue_constraint_extraction(
        session=session,
        text="I do deep work in the mornings.",
        reason="test",
        is_initial=False,
    )
    assert task is not None
    res = await asyncio.wait_for(task, timeout=1.0)
    assert res is not None
    assert not session.pending_constraint_extractions


@pytest.mark.asyncio
async def test_queue_constraint_extraction_respects_classifier():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_extraction_tasks = {}
    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)

    async def _fake_interpret(_self, _session, *, text: str, is_initial: bool):
        assert text
        return ConstraintInterpretation(
            should_extract=False,
            scope="session",
            constraints=[],
        )

    class _Store:
        async def add_constraints(self, **_kwargs):
            return []

    async def _fake_collect_constraints(_session: Session):
        return None

    agent._interpret_constraints = types.MethodType(_fake_interpret, agent)  # type: ignore[assignment]
    async def _noop_store() -> None:
        return None

    agent._ensure_constraint_store = _noop_store  # type: ignore[assignment]
    agent._constraint_store = _Store()
    agent._collect_constraints = _fake_collect_constraints  # type: ignore[assignment]

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
    )

    task = agent._queue_constraint_extraction(
        session=session,
        text="Start timeboxing",
        reason="test",
        is_initial=True,
    )
    assert task is not None
    res = await asyncio.wait_for(task, timeout=1.0)
    assert res is None
