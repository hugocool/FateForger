"""Unit tests for Stage 2 skeleton pre-generation behavior."""

from __future__ import annotations

import asyncio
import types
from datetime import date, time, timedelta

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.nodes.nodes import StageSkeletonNode, TransitionNode
from fateforger.agents.timeboxing.tb_models import ET, FixedWindow, TBEvent, TBPlan
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

    async def _should_not_run_draft(_session: Session) -> tuple[None, str, TBPlan | None]:
        raise AssertionError("Synchronous skeleton draft should not run.")
    agent._build_remote_snapshot_plan = types.MethodType(  # type: ignore[attr-defined]
        lambda self, _session: None,
        agent,
    )
    agent._render_markdown_summary_blocks = types.MethodType(  # type: ignore[attr-defined]
        lambda self, text: [],
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
    session.pre_generated_skeleton_plan = TBPlan(
        date=date(2026, 2, 13),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Focus Block",
                t=ET.DW,
                p=FixedWindow(st=time(9, 0), et=time(10, 30)),
            )
        ],
    )
    session.pre_generated_skeleton_markdown = "## Day Overview\n- Focus Block"
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

    assert session.timebox is None
    assert session.tb_plan is not None
    assert session.base_snapshot is None
    assert session.skeleton_overview_markdown == "## Day Overview\n- Focus Block"
    assert session.stage_ready is True
    assert session.last_response == "Stage 3/5 (Skeleton)\nOverview ready below."
    assert session.pre_generated_skeleton is None
    assert session.pre_generated_skeleton_plan is None
    assert session.pre_generated_skeleton_markdown is None


@pytest.mark.asyncio
async def test_consume_pre_generated_skeleton_waits_for_inflight_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consume should await matching in-flight pre-generation before drafting sync."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t2",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
        frame_facts={"immovables": [{"title": "Meeting", "start": "10:00", "end": "11:00"}]},
        input_facts={"block_plan": {"deep_blocks": 2}},
    )
    expected_plan = TBPlan(
        date=date(2026, 2, 14),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Focus Block",
                t=ET.DW,
                p=FixedWindow(st=time(9, 0), et=time(10, 30)),
            )
        ],
    )
    session.pre_generated_skeleton_fingerprint = (
        agent._skeleton_pregeneration_fingerprint(session)  # type: ignore[attr-defined]
    )

    async def _background_complete() -> None:
        await asyncio.sleep(0.01)
        session.pre_generated_skeleton_plan = expected_plan
        session.pre_generated_skeleton_markdown = "## Day Overview\n- Focus Block"

    session.pre_generated_skeleton_task = asyncio.create_task(_background_complete())

    async def _should_not_run_sync_draft(
        self: TimeboxingFlowAgent, current: Session
    ) -> tuple[None, str, TBPlan | None]:
        _ = (self, current)
        raise AssertionError("Synchronous skeleton draft should not run.")

    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_run_skeleton_draft",
        _should_not_run_sync_draft,
    )

    _timebox, markdown, drafted_plan = await TimeboxingFlowAgent._consume_pre_generated_skeleton(
        agent, session
    )

    assert drafted_plan is expected_plan
    assert markdown == "## Day Overview\n- Focus Block"
