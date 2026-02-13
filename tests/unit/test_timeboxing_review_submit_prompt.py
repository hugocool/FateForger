"""Unit tests for Stage 2 pre-gen trigger and Stage 5 submit prompt behavior."""

from __future__ import annotations

import types
from datetime import date, time, timedelta
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.nodes.nodes import (
    StageCaptureInputsNode,
    StageReviewCommitNode,
    TransitionNode,
)
from fateforger.agents.timeboxing.stage_gating import StageGateOutput, TimeboxingStage
from fateforger.agents.timeboxing.timebox import Timebox


def _build_transition() -> TransitionNode:
    """Return a minimal transition node stub for stage node tests."""
    transition = TransitionNode.__new__(TransitionNode)
    transition.stage_user_message = "test"
    transition.decision = None
    return transition


@pytest.mark.asyncio
async def test_stage_capture_inputs_queues_skeleton_pre_generation() -> None:
    """Stage 2 should trigger background skeleton pre-generation hook."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._queue_skeleton_pre_generation = AsyncMock()

    async def _run_stage_gate(*, stage, user_message, context) -> StageGateOutput:
        return StageGateOutput(
            stage_id=stage,
            ready=True,
            summary=["ok"],
            missing=[],
            question=None,
            facts={"block_plan": {"deep_blocks": 2}},
        )

    def _build_context(_session, *, user_message):
        return {"user_message": user_message}

    agent._run_stage_gate = types.MethodType(  # type: ignore[attr-defined]
        lambda self, **kwargs: _run_stage_gate(**kwargs),
        agent,
    )
    agent._build_capture_inputs_context = types.MethodType(  # type: ignore[attr-defined]
        lambda self, session, user_message: _build_context(session, user_message=user_message),
        agent,
    )
    agent._queue_skeleton_pre_generation = types.MethodType(  # type: ignore[attr-defined]
        lambda self, session: None,
        agent,
    )

    called = {"value": False}

    def _queue(_session: Session) -> None:
        called["value"] = True

    agent._queue_skeleton_pre_generation = types.MethodType(  # type: ignore[attr-defined]
        lambda self, session: _queue(session),
        agent,
    )

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    node = StageCaptureInputsNode(
        orchestrator=agent,
        session=session,
        transition=_build_transition(),
    )
    await node.on_messages(
        [TextMessage(content="test", source="user")],
        CancellationToken(),
    )

    assert called["value"] is True


@pytest.mark.asyncio
async def test_stage_review_sets_pending_submit_without_auto_submit() -> None:
    """Stage 5 should not auto-submit, and should set pending submit state."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _run_review_commit(*, timebox) -> StageGateOutput:
        return StageGateOutput(
            stage_id=TimeboxingStage.REVIEW_COMMIT,
            ready=True,
            summary=["reviewed"],
            missing=[],
            question=None,
            facts={},
        )

    agent._run_review_commit = types.MethodType(  # type: ignore[attr-defined]
        lambda self, **kwargs: _run_review_commit(**kwargs),
        agent,
    )
    submitter = AsyncMock()
    agent._calendar_submitter = types.SimpleNamespace(submit_plan=submitter)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.timebox = Timebox(
        events=[
            CalendarEvent(
                summary="Focus",
                event_type=EventType.DEEP_WORK,
                start_time=time(9, 0),
                duration=timedelta(minutes=90),
            )
        ],
        date=date(2026, 2, 13),
        timezone="Europe/Amsterdam",
    )

    node = StageReviewCommitNode(
        orchestrator=agent,
        session=session,
        transition=_build_transition(),
    )
    await node.on_messages(
        [TextMessage(content="proceed", source="user")],
        CancellationToken(),
    )

    assert session.pending_submit is True
    submitter.assert_not_called()
