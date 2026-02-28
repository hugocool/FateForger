"""Regression tests for deterministic session registration before async interpretation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.messages import StartTimeboxing, TimeboxingUserReply
from fateforger.agents.timeboxing.stage_gating import StageDecision, TimeboxingStage


def _build_agent() -> TimeboxingFlowAgent:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    agent._session_key = lambda _ctx, fallback=None: fallback or "default"
    agent._default_tz_name = lambda: "UTC"
    agent._default_planned_date = lambda *, now, tz: "2026-02-27"
    agent._queue_constraint_prefetch = lambda _session: None
    agent._reset_durable_prefetch_state = lambda _session: None
    agent._session_debug = lambda *_args, **_kwargs: None
    agent._build_commit_prompt_blocks = lambda *, session: TextMessage(
        content=f"commit:{session.thread_ts}",
        source="timeboxing_agent",
    )
    agent._refresh_temporal_facts = lambda _session: None

    async def _prefetch_calendar_immovables(_session, _planned_date):
        return None

    agent._prefetch_calendar_immovables = _prefetch_calendar_immovables

    async def _prime_collect_prefetch_non_blocking(*, session, planned_date, blocking):  # noqa: ARG001
        return None

    async def _run_graph_turn(*, session, user_text):  # noqa: ARG001
        return TextMessage(content="graph-progressed", source="timeboxing_agent")

    async def _maybe_wrap_constraint_review(*, reply, session):  # noqa: ARG001
        return reply

    async def _publish_update(*, session, user_message, actions):  # noqa: ARG001
        return None

    agent._prime_collect_prefetch_non_blocking = _prime_collect_prefetch_non_blocking
    agent._run_graph_turn = _run_graph_turn
    agent._maybe_wrap_constraint_review = _maybe_wrap_constraint_review
    agent._attach_presenter_blocks = lambda *, reply, session: reply
    agent._publish_update = _publish_update

    class _SchedulerPrefetch:
        def __init__(self) -> None:
            self.initial_prefetch_calls = 0

        def queue_initial_prefetch(self, *, session, planned_date):  # noqa: ARG002
            self.initial_prefetch_calls += 1
            return None

        async def ensure_collect_stage_ready(self, *, session):  # noqa: ARG002
            return None

    agent._scheduler_prefetch = _SchedulerPrefetch()
    return agent


def test_session_key_prefers_thread_fallback_over_topic_source() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    ctx = SimpleNamespace(
        topic_id=SimpleNamespace(source="topic-key"),
        sender=SimpleNamespace(key="sender-key"),
    )
    assert agent._session_key(ctx, fallback="thread-1") == "thread-1"
    assert agent._session_key(ctx) == "topic-key"


@pytest.mark.asyncio
async def test_on_start_registers_session_before_date_interpretation() -> None:
    agent = _build_agent()
    seen: dict[str, bool] = {}

    async def _interpret(text: str, *, now, tz_name):  # noqa: ARG001
        seen["session_present"] = "thread-1" in agent._sessions
        await asyncio.sleep(0)
        return "2026-03-01"

    agent._interpret_planned_date = _interpret

    out = await TimeboxingFlowAgent.on_start(
        agent,
        StartTimeboxing(
            channel_id="C1",
            thread_ts="thread-1",
            user_id="U1",
            user_input="timebox today",
        ),
        SimpleNamespace(),
    )

    assert isinstance(out, TextMessage)
    assert seen == {"session_present": True}
    assert agent._sessions["thread-1"].planned_date == "2026-03-01"


@pytest.mark.asyncio
async def test_on_user_reply_registers_session_before_date_interpretation() -> None:
    agent = _build_agent()
    seen: dict[str, bool] = {}

    async def _interpret(text: str, *, now, tz_name):  # noqa: ARG001
        seen["session_present"] = "thread-2" in agent._sessions
        await asyncio.sleep(0)
        return "2026-03-02"

    agent._interpret_planned_date = _interpret

    out = await TimeboxingFlowAgent.on_user_reply(
        agent,
        TimeboxingUserReply(
            channel_id="C1",
            thread_ts="thread-2",
            user_id="U1",
            text="tomorrow",
        ),
        SimpleNamespace(),
    )

    assert isinstance(out, TextMessage)
    assert out.content == "graph-progressed"
    assert seen == {"session_present": True}
    assert agent._sessions["thread-2"].planned_date == "2026-03-02"
    assert agent._sessions["thread-2"].committed is True


@pytest.mark.asyncio
async def test_on_user_reply_in_thread_commits_existing_session_without_button() -> None:
    agent = _build_agent()
    session = Session(
        thread_ts="thread-3",
        channel_id="C1",
        user_id="U1",
        committed=False,
        planned_date="2026-02-27",
        tz_name="UTC",
        session_key="thread-3",
    )
    agent._sessions["thread-3"] = session

    async def _interpret(text: str, *, now, tz_name):  # noqa: ARG001
        return "2026-02-27"

    async def _prime_collect_prefetch_non_blocking(*, session, planned_date, blocking):  # noqa: ARG001
        return None

    async def _run_graph_turn(*, session, user_text):  # noqa: ARG001
        return TextMessage(content="graph-progressed", source="timeboxing_agent")

    async def _maybe_wrap_constraint_review(*, reply, session):  # noqa: ARG001
        return reply

    async def _publish_update(*, session, user_message, actions):  # noqa: ARG001
        return None

    agent._interpret_planned_date = _interpret
    agent._prime_collect_prefetch_non_blocking = _prime_collect_prefetch_non_blocking
    agent._run_graph_turn = _run_graph_turn
    agent._maybe_wrap_constraint_review = _maybe_wrap_constraint_review
    agent._attach_presenter_blocks = lambda *, reply, session: reply
    agent._publish_update = _publish_update

    ctx = SimpleNamespace(topic_id=SimpleNamespace(source="other-routing-key"), sender=None)
    out = await TimeboxingFlowAgent.on_user_reply(
        agent,
        TimeboxingUserReply(
            channel_id="C1",
            thread_ts="thread-3",
            user_id="U1",
            text="Today. Wake 12:00, no fixed plans.",
        ),
        ctx,
    )

    assert isinstance(out, TextMessage)
    assert out.content == "graph-progressed"
    assert session.committed is True


@pytest.mark.asyncio
async def test_on_user_reply_serializes_implicit_commit_for_rapid_replies() -> None:
    agent = _build_agent()
    session = Session(
        thread_ts="thread-rapid",
        channel_id="C1",
        user_id="U1",
        committed=False,
        planned_date="2026-02-27",
        tz_name="UTC",
        session_key="thread-rapid",
    )
    agent._sessions["thread-rapid"] = session
    interpret_count = {"count": 0}

    async def _interpret(text: str, *, now, tz_name):  # noqa: ARG001
        interpret_count["count"] += 1
        await asyncio.sleep(0.05)
        return "2026-02-27"

    async def _run_graph_turn(*, session, user_text):  # noqa: ARG001
        await asyncio.sleep(0.01)
        return TextMessage(content=f"graph:{user_text}", source="timeboxing_agent")

    agent._interpret_planned_date = _interpret
    agent._run_graph_turn = _run_graph_turn

    ctx = SimpleNamespace(topic_id=SimpleNamespace(source="other-routing-key"), sender=None)
    first = TimeboxingUserReply(
        channel_id="C1",
        thread_ts="thread-rapid",
        user_id="U1",
        text="First fast reply",
    )
    second = TimeboxingUserReply(
        channel_id="C1",
        thread_ts="thread-rapid",
        user_id="U1",
        text="Second fast reply",
    )
    out1, out2 = await asyncio.gather(
        TimeboxingFlowAgent.on_user_reply(agent, first, ctx),
        TimeboxingFlowAgent.on_user_reply(agent, second, ctx),
    )

    assert isinstance(out1, TextMessage)
    assert isinstance(out2, TextMessage)
    assert interpret_count["count"] == 1
    assert session.committed is True


@pytest.mark.asyncio
async def test_on_user_reply_implicit_commit_does_not_duplicate_prefetch_queue() -> None:
    agent = _build_agent()
    session = Session(
        thread_ts="thread-prefetch",
        channel_id="C1",
        user_id="U1",
        committed=False,
        planned_date="2026-02-27",
        tz_name="UTC",
        session_key="thread-prefetch",
    )
    agent._sessions["thread-prefetch"] = session

    async def _interpret(text: str, *, now, tz_name):  # noqa: ARG001
        return "2026-02-27"

    async def _run_graph_turn(*, session, user_text):  # noqa: ARG001
        return TextMessage(content="graph-progressed", source="timeboxing_agent")

    agent._interpret_planned_date = _interpret
    agent._run_graph_turn = _run_graph_turn

    out = await TimeboxingFlowAgent.on_user_reply(
        agent,
        TimeboxingUserReply(
            channel_id="C1",
            thread_ts="thread-prefetch",
            user_id="U1",
            text="Today, continue in this thread.",
        ),
        SimpleNamespace(),
    )

    assert isinstance(out, TextMessage)
    assert out.content == "graph-progressed"
    assert agent._scheduler_prefetch.initial_prefetch_calls == 0


@pytest.mark.asyncio
async def test_on_user_reply_review_commit_proceed_submits_without_button() -> None:
    agent = _build_agent()
    session = Session(
        thread_ts="thread-submit",
        channel_id="C1",
        user_id="U1",
        committed=True,
        planned_date="2026-02-27",
        tz_name="UTC",
        session_key="thread-submit",
    )
    session.stage = TimeboxingStage.REVIEW_COMMIT
    session.pending_submit = True
    agent._sessions["thread-submit"] = session
    calls = {"submit": 0, "run_graph": 0}

    async def _decide_next_action(session_obj: Session, *, user_message: str):  # noqa: ARG001
        _ = session_obj
        return StageDecision(action="proceed")

    async def _submit_pending_plan(*, session: Session):
        _ = session
        calls["submit"] += 1
        return TextMessage(content="submitted-from-nl", source="timeboxing_agent")

    async def _run_graph_turn(*, session: Session, user_text: str):  # noqa: ARG001
        _ = session, user_text
        calls["run_graph"] += 1
        return TextMessage(content="graph-progressed", source="timeboxing_agent")

    agent._decide_next_action = _decide_next_action
    agent._submit_pending_plan = _submit_pending_plan
    agent._run_graph_turn = _run_graph_turn

    out = await TimeboxingFlowAgent.on_user_reply(
        agent,
        TimeboxingUserReply(
            channel_id="C1",
            thread_ts="thread-submit",
            user_id="U1",
            text="Proceed and commit now.",
        ),
        SimpleNamespace(),
    )

    assert isinstance(out, TextMessage)
    assert out.content == "submitted-from-nl"
    assert calls["submit"] == 1
    assert calls["run_graph"] == 0
