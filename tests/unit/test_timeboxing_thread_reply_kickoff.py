"""Unit tests for natural-language thread reply kickoff in timeboxing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.messages import TimeboxingUserReply
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


class _Ctx:
    topic_id = None
    sender = None


def _build_agent() -> TimeboxingFlowAgent:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    agent._session_debug = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    agent._default_tz_name = lambda: "Europe/Amsterdam"  # type: ignore[attr-defined]
    agent._interpret_planned_date = AsyncMock(return_value="2026-02-28")  # type: ignore[attr-defined]
    agent._prefetch_calendar_immovables = AsyncMock()  # type: ignore[attr-defined]
    agent._queue_constraint_prefetch = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    agent._await_pending_durable_constraint_prefetch = AsyncMock()  # type: ignore[attr-defined]
    agent._ensure_calendar_immovables = AsyncMock()  # type: ignore[attr-defined]
    agent._is_collect_stage_loaded = lambda *_args, **_kwargs: True  # type: ignore[attr-defined]
    agent._run_graph_turn = AsyncMock(
        return_value=TextMessage(content="stage advanced", source="timeboxing_agent")
    )  # type: ignore[attr-defined]
    agent._maybe_wrap_constraint_review = AsyncMock(
        side_effect=lambda *, reply, session: reply
    )  # type: ignore[attr-defined]
    agent._attach_presenter_blocks = lambda *, reply, session: reply  # type: ignore[attr-defined]
    agent._publish_update = AsyncMock()  # type: ignore[attr-defined]
    agent._reset_durable_prefetch_state = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    return agent


@pytest.mark.asyncio
async def test_missing_session_thread_reply_commits_and_advances() -> None:
    """Missing session in-thread should commit implicitly and run stage flow."""
    agent = _build_agent()
    agent._build_commit_prompt_blocks = lambda **_kwargs: (_ for _ in ()).throw(  # type: ignore[attr-defined]
        AssertionError("Should not return Stage-0 commit prompt for in-thread NL kickoff.")
    )

    result = await agent.on_user_reply(
        TimeboxingUserReply(
            channel_id="C1",
            thread_ts="T1",
            user_id="U1",
            text="Today. Wake 12:00, focused work day.",
        ),
        _Ctx(),
    )

    assert isinstance(result, TextMessage)
    session = agent._sessions["T1"]
    assert session.committed is True
    assert session.stage == TimeboxingStage.COLLECT_CONSTRAINTS
    agent._run_graph_turn.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_existing_uncommitted_thread_reply_commits_and_advances() -> None:
    """Existing uncommitted session should commit implicitly and proceed in same turn."""
    agent = _build_agent()
    session = Session(thread_ts="T1", channel_id="C1", user_id="U1")
    session.committed = False
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    session.planned_date = "2026-02-27"
    session.tz_name = "Europe/Amsterdam"
    agent._sessions["T1"] = session

    result = await agent.on_user_reply(
        TimeboxingUserReply(
            channel_id="C1",
            thread_ts="T1",
            user_id="U1",
            text="Actually let's do tomorrow.",
        ),
        _Ctx(),
    )

    assert isinstance(result, TextMessage)
    assert session.committed is True
    assert session.planned_date == "2026-02-28"
    agent._run_graph_turn.assert_awaited_once()  # type: ignore[attr-defined]
