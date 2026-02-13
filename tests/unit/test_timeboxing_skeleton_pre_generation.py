"""Unit tests for Stage 2 skeleton pre-generation behavior."""

from __future__ import annotations

import types
from datetime import date, time, timedelta

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.nodes.nodes import StageSkeletonNode, TransitionNode
from fateforger.agents.timeboxing.stage_gating import StageGateOutput, TimeboxingStage
from fateforger.agents.timeboxing.timebox import Timebox


@pytest.mark.asyncio
async def test_stage_skeleton_uses_pre_generated_draft_without_llm() -> None:
    """Use ``session.pre_generated_skeleton`` and skip synchronous LLM drafting."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    pre_generated = Timebox(
        events=[
            CalendarEvent(
                summary="Focus Block",
                event_type=EventType.DEEP_WORK,
                start_time=time(9, 0),
                duration=timedelta(minutes=90),
            )
        ],
        date=date(2026, 2, 13),
        timezone="Europe/Amsterdam",
    )

    async def _run_review_summary(*, stage, timebox) -> StageGateOutput:
        return StageGateOutput(
            stage_id=stage,
            ready=True,
            summary=["Summary ready"],
            missing=[],
            question=None,
            facts={},
        )

    async def _should_not_run_draft(_session: Session) -> Timebox:
        raise AssertionError("Synchronous skeleton draft should not run.")

    agent._run_timebox_summary = types.MethodType(  # type: ignore[attr-defined]
        lambda self, **kwargs: _run_review_summary(**kwargs),
        agent,
    )
    agent._run_skeleton_draft = types.MethodType(  # type: ignore[attr-defined]
        lambda self, session: _should_not_run_draft(session),
        agent,
    )

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-13",
        tz_name="Europe/Amsterdam",
        frame_facts={"immovables": [{"title": "Meeting", "start": "10:00", "end": "11:00"}]},
        input_facts={"block_plan": {"deep_blocks": 2}},
    )
    session.pre_generated_skeleton = pre_generated
    session.pre_generated_skeleton_fingerprint = (
        agent._skeleton_pregeneration_fingerprint(session)  # type: ignore[attr-defined]
    )

    transition = TransitionNode.__new__(TransitionNode)
    transition.stage_user_message = ""
    transition.decision = None

    node = StageSkeletonNode(
        orchestrator=agent,
        session=session,
        transition=transition,
    )
    await node.on_messages(
        [TextMessage(content="go", source="user")],
        CancellationToken(),
    )

    assert session.timebox is not None
    assert session.timebox.events[0].summary == "Focus Block"
    assert session.tb_plan is not None
    assert session.base_snapshot is not None
    assert session.stage_ready is True
    assert session.pre_generated_skeleton is None
