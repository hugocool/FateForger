"""Unit tests for deterministic stage-control actions."""

from __future__ import annotations

from datetime import date, time
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.messages import TimeboxingStageAction
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.timeboxing_stage_actions import (
    FF_TIMEBOX_STAGE_BACK_ACTION_ID,
    FF_TIMEBOX_STAGE_CANCEL_ACTION_ID,
    FF_TIMEBOX_STAGE_PROCEED_ACTION_ID,
    FF_TIMEBOX_STAGE_REDO_ACTION_ID,
)
from fateforger.slack_bot.timeboxing_submit import FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID
from fateforger.agents.timeboxing.tb_models import FixedWindow, TBEvent, TBPlan


class _Ctx:
    """Minimal message context test double."""

    topic_id = None
    sender = None


def _review_plan() -> TBPlan:
    """Return a minimal valid plan fixture for review-stage block rendering."""
    return TBPlan(
        events=[
            TBEvent(
                n="Focus",
                d="",
                t="DW",
                p=FixedWindow(st=time(9, 0), et=time(10, 0)),
            )
        ],
        date=date(2026, 2, 27),
        tz="Europe/Amsterdam",
    )


def _collect_action_ids(blocks: list[dict]) -> list[str]:
    """Extract action IDs from Slack action blocks."""
    return [
        str(element.get("action_id"))
        for block in blocks
        for element in (block.get("elements") or [])
        if isinstance(element, dict)
    ]


def test_render_stage_action_blocks_ready_includes_proceed() -> None:
    """Ready stages should include deterministic Proceed button."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.stage = TimeboxingStage.CAPTURE_INPUTS
    session.stage_ready = True

    blocks = agent._render_stage_action_blocks(session=session)
    action_ids = _collect_action_ids(blocks)
    assert FF_TIMEBOX_STAGE_PROCEED_ACTION_ID in action_ids
    assert FF_TIMEBOX_STAGE_BACK_ACTION_ID in action_ids
    assert FF_TIMEBOX_STAGE_REDO_ACTION_ID in action_ids
    assert FF_TIMEBOX_STAGE_CANCEL_ACTION_ID in action_ids


def test_render_stage_action_blocks_not_ready_hides_proceed() -> None:
    """Proceed should be hidden until stage criteria are met."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.stage = TimeboxingStage.CAPTURE_INPUTS
    session.stage_ready = False

    blocks = agent._render_stage_action_blocks(session=session)
    action_ids = _collect_action_ids(blocks)
    assert FF_TIMEBOX_STAGE_PROCEED_ACTION_ID not in action_ids
    assert FF_TIMEBOX_STAGE_BACK_ACTION_ID in action_ids
    assert FF_TIMEBOX_STAGE_REDO_ACTION_ID in action_ids
    assert FF_TIMEBOX_STAGE_CANCEL_ACTION_ID in action_ids


@pytest.mark.asyncio
async def test_stage_action_proceed_requires_stage_ready() -> None:
    """Proceed action should be rejected until stage criteria are met."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    session.stage_ready = False
    session.stage_missing = ["timezone", "work window"]
    session.stage_question = "What timezone should I use?"
    agent._sessions["t1"] = session

    agent._attach_presenter_blocks = TimeboxingFlowAgent._attach_presenter_blocks.__get__(  # type: ignore[attr-defined]
        agent, TimeboxingFlowAgent
    )
    agent._publish_update = AsyncMock()  # type: ignore[attr-defined]

    response = await agent.on_stage_action(
        TimeboxingStageAction(
            channel_id="c1",
            thread_ts="t1",
            user_id="u1",
            action="proceed",
        ),
        _Ctx(),
    )

    assert isinstance(response, SlackBlockMessage)
    assert "Cannot proceed yet" in response.text
    assert "timezone" in response.text
    action_ids = _collect_action_ids(response.blocks)
    assert FF_TIMEBOX_STAGE_PROCEED_ACTION_ID not in action_ids
    assert session.stage == TimeboxingStage.COLLECT_CONSTRAINTS


@pytest.mark.asyncio
async def test_stage_action_proceed_advances_and_replaces_message() -> None:
    """Proceed action should advance stage and return the next rendered message."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    session.stage_ready = True
    agent._sessions["t1"] = session

    async def _run_graph_turn(*, session: Session, user_text: str) -> TextMessage:
        _ = (session, user_text)
        assert session.force_stage_rerun is True
        assert user_text == ""
        return TextMessage(content="Stage 2/5 (CaptureInputs)", source="PresenterNode")

    async def _wrap(reply: TextMessage, *, session: Session) -> TextMessage:
        _ = session
        return reply

    agent._run_graph_turn = AsyncMock(side_effect=_run_graph_turn)  # type: ignore[attr-defined]
    agent._maybe_wrap_constraint_review = AsyncMock(side_effect=_wrap)  # type: ignore[attr-defined]
    agent._attach_presenter_blocks = lambda *, reply, session: reply  # type: ignore[attr-defined]
    agent._publish_update = AsyncMock()  # type: ignore[attr-defined]

    response = await agent.on_stage_action(
        TimeboxingStageAction(
            channel_id="c1",
            thread_ts="t1",
            user_id="u1",
            action="proceed",
        ),
        _Ctx(),
    )

    assert isinstance(response, TextMessage)
    assert "CaptureInputs" in response.content
    assert session.stage == TimeboxingStage.CAPTURE_INPUTS


def test_attach_presenter_blocks_review_stage_includes_submit_actions() -> None:
    """Review stage should expose explicit submit controls and mark pending submit."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.stage = TimeboxingStage.REVIEW_COMMIT
    session.stage_ready = True
    session.tb_plan = _review_plan()
    session.base_snapshot = _review_plan()
    session.pending_presenter_blocks = []

    wrapped = agent._attach_presenter_blocks(
        reply=TextMessage(content="review", source="PresenterNode"),
        session=session,
    )

    assert isinstance(wrapped, SlackBlockMessage)
    action_ids = _collect_action_ids(wrapped.blocks)
    assert FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID in action_ids
    assert session.pending_submit is True


def test_attach_presenter_blocks_appends_stage_actions() -> None:
    """Presenter output should include deterministic stage-control actions."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.stage = TimeboxingStage.CAPTURE_INPUTS
    session.stage_ready = True
    session.pending_presenter_blocks = []

    wrapped = agent._attach_presenter_blocks(
        reply=TextMessage(content="hello", source="PresenterNode"),
        session=session,
    )

    assert isinstance(wrapped, SlackBlockMessage)
    action_ids = _collect_action_ids(wrapped.blocks)
    assert FF_TIMEBOX_STAGE_PROCEED_ACTION_ID in action_ids
