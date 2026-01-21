import asyncio

import pytest
import types

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent


@pytest.mark.asyncio
async def test_queue_constraint_extraction_runs_in_background():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_extraction_tasks = {}
    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)
    agent._constraint_intent_agent = None

    done = asyncio.Event()

    async def _fake_should_extract(_self, _text, *, is_initial):
        return True

    async def _fake_extract(_session, _text):
        done.set()
        return None

    agent._should_extract_constraints = types.MethodType(
        _fake_should_extract, agent
    )
    agent._extract_constraints = _fake_extract

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
    await asyncio.wait_for(done.wait(), timeout=1.0)
    await asyncio.gather(task, return_exceptions=True)
    assert not session.pending_constraint_extractions


@pytest.mark.asyncio
async def test_queue_constraint_extraction_respects_classifier():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_extraction_tasks = {}
    agent._constraint_extraction_semaphore = asyncio.Semaphore(1)
    agent._constraint_intent_agent = None

    called = False

    async def _fake_should_extract(_self, _text, *, is_initial):
        return False

    async def _fake_extract(_session, _text):
        nonlocal called
        called = True
        return None

    agent._should_extract_constraints = types.MethodType(
        _fake_should_extract, agent
    )
    agent._extract_constraints = _fake_extract

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
    await asyncio.gather(task, return_exceptions=True)
    assert called is False
