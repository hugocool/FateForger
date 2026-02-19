import types

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.messages import TimeboxingCommitDate


class _Ctx:
    topic_id = None
    sender = None


@pytest.mark.asyncio
async def test_commit_does_not_trigger_initial_extraction():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    agent._durable_constraint_prefetch_tasks = {}

    async def _fake_run_graph_turn(*, session, user_text):
        return TextMessage(content="ok", source="timeboxing_agent")

    async def _fake_publish_update(**_kwargs):
        return None

    async def _fake_prefetch_calendar(_session, _date):
        return None

    agent._run_graph_turn = _fake_run_graph_turn
    agent._publish_update = _fake_publish_update
    agent._prefetch_calendar_immovables = _fake_prefetch_calendar
    agent._apply_prefetched_calendar_immovables = lambda _session: None
    agent._queue_constraint_prefetch = lambda _session: None

    called = False

    def _fake_queue_constraint_extraction(**_kwargs):
        nonlocal called
        called = True
        return None

    agent._queue_constraint_extraction = _fake_queue_constraint_extraction

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        last_user_message="Start timeboxing",
    )
    agent._sessions["t1"] = session

    msg = TimeboxingCommitDate(
        channel_id="c1",
        thread_ts="t1",
        user_id="u1",
        planned_date="2026-01-21",
        timezone="Europe/Amsterdam",
    )

    await agent.on_commit_date(msg, _Ctx())

    assert called is False
