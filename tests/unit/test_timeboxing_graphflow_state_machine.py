import asyncio
import types

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.flow_graph import build_timeboxing_graphflow
from fateforger.agents.timeboxing.nodes.nodes import TransitionNode
from fateforger.agents.timeboxing.patching import TimeboxPatcher
from fateforger.agents.timeboxing.stage_gating import StageDecision, StageGateOutput, TimeboxingStage


@pytest.mark.asyncio
async def test_graphflow_routes_by_session_stage_and_decision():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._timebox_patcher = TimeboxPatcher()
    agent._durable_constraint_prefetch_tasks = {}

    async def _noop_calendar(_self, _session: Session, *, timeout_s: float = 0.0) -> None:
        return None

    def _noop_queue_extract(**_kwargs):
        return None

    async def _fake_decide(_self, _session: Session, *, user_message: str) -> StageDecision:
        assert user_message == "hello"
        return StageDecision(action="provide_info")

    async def _fake_stage_gate(
        _self,
        *,
        stage: TimeboxingStage,
        user_message: str,
        context: dict,
    ) -> StageGateOutput:
        assert user_message == "hello"
        return StageGateOutput(
            stage_id=stage,
            ready=False,
            summary=[f"saw:{stage.value}"],
            missing=["x"],
            question="q?",
            facts={},
        )

    def _fake_format(_self, gate: StageGateOutput, **_kwargs) -> str:
        return f"{gate.stage_id.value}:{gate.ready}:{gate.question}"

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._queue_constraint_extraction = _noop_queue_extract  # type: ignore[assignment]
    agent._decide_next_action = types.MethodType(_fake_decide, agent)
    agent._run_stage_gate = types.MethodType(_fake_stage_gate, agent)
    agent._collect_background_notes = lambda _session: None  # type: ignore[assignment]
    agent._format_stage_message = types.MethodType(_fake_format, agent)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS

    flow = build_timeboxing_graphflow(orchestrator=agent, session=session)

    out: TextMessage | None = None
    async for item in flow.run_stream(task=TextMessage(content="hello", source="user")):
        if isinstance(item, TextMessage) and item.source == "PresenterNode":
            out = item
    assert out is not None
    assert out.content.startswith("CollectConstraints:")


@pytest.mark.asyncio
async def test_graphflow_proceed_advances_to_next_stage():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._timebox_patcher = TimeboxPatcher()
    agent._durable_constraint_prefetch_tasks = {}

    async def _noop_calendar(_self, _session: Session, *, timeout_s: float = 0.0) -> None:
        return None

    def _noop_queue_extract(**_kwargs):
        return None

    async def _fake_decide(_self, _session: Session, *, user_message: str) -> StageDecision:
        return StageDecision(action="proceed")

    async def _fake_stage_gate(
        _self,
        *,
        stage: TimeboxingStage,
        user_message: str,
        context: dict,
    ) -> StageGateOutput:
        # proceed runs next stage with empty user_message
        assert user_message == ""
        return StageGateOutput(
            stage_id=stage,
            ready=True,
            summary=[f"ran:{stage.value}"],
            missing=[],
            question=None,
            facts={},
        )

    def _fake_format(_self, gate: StageGateOutput, **_kwargs) -> str:
        return f"STAGE={gate.stage_id.value}"

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._queue_constraint_extraction = _noop_queue_extract  # type: ignore[assignment]
    agent._decide_next_action = types.MethodType(_fake_decide, agent)
    agent._run_stage_gate = types.MethodType(_fake_stage_gate, agent)
    agent._collect_background_notes = lambda _session: None  # type: ignore[assignment]
    agent._format_stage_message = types.MethodType(_fake_format, agent)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    session.stage_ready = True  # allow proceed without override

    flow = build_timeboxing_graphflow(orchestrator=agent, session=session)
    out: TextMessage | None = None
    async for item in flow.run_stream(task=TextMessage(content="go", source="user")):
        if isinstance(item, TextMessage) and item.source == "PresenterNode":
            out = item
    assert out is not None
    assert out.content == "STAGE=CaptureInputs"


@pytest.mark.asyncio
async def test_graphflow_cancel_terminates_without_stage_run():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._timebox_patcher = TimeboxPatcher()

    async def _noop_calendar(_self, _session: Session, *, timeout_s: float = 0.0) -> None:
        return None

    def _noop_queue_extract(**_kwargs):
        return None

    async def _fake_decide(_self, _session: Session, *, user_message: str) -> StageDecision:
        return StageDecision(action="cancel")

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._queue_constraint_extraction = _noop_queue_extract  # type: ignore[assignment]
    agent._decide_next_action = types.MethodType(_fake_decide, agent)
    agent._collect_background_notes = lambda _session: None  # type: ignore[assignment]

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.CAPTURE_INPUTS

    flow = build_timeboxing_graphflow(orchestrator=agent, session=session)
    out: TextMessage | None = None
    async for item in flow.run_stream(task=TextMessage(content="stop", source="user")):
        if isinstance(item, TextMessage) and item.source == "PresenterNode":
            out = item
    assert out is not None
    assert out.content == "Okay—stopping this timeboxing session."
    assert session.thread_state == "canceled"


@pytest.mark.asyncio
async def test_transition_routes_reviewcommit_edits_back_to_refine():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _advance_stage(
        _self,
        session: Session,
        *,
        next_stage: TimeboxingStage,
    ) -> None:
        session.stage = next_stage

    agent._advance_stage = types.MethodType(_advance_stage, agent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.REVIEW_COMMIT

    turn_init = types.SimpleNamespace(
        turn=types.SimpleNamespace(
            decision=StageDecision(action="provide_info"),
            user_text="please add a lunch block and a buffer",
        )
    )
    node = TransitionNode(orchestrator=agent, session=session, turn_init=turn_init)

    await node.on_messages([], cancellation_token=types.SimpleNamespace())

    assert session.stage == TimeboxingStage.REFINE
    assert node.stage_user_message == "please add a lunch block and a buffer"
