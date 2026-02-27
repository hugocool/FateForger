import types

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import CalendarSyncOutcome
from fateforger.agents.timeboxing.agent import RefinePreflight
from fateforger.agents.timeboxing.agent import RefineToolExecutionOutcome
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.flow_graph import build_timeboxing_graphflow
from fateforger.agents.timeboxing.nodes.nodes import (
    DecisionNode,
    StageRefineNode,
    TransitionNode,
)
from fateforger.agents.timeboxing.patching import TimeboxPatcher
from fateforger.agents.timeboxing.stage_gating import StageDecision, StageGateOutput, TimeboxingStage


@pytest.mark.asyncio
async def test_graphflow_routes_by_session_stage_and_decision():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._timebox_patcher = TimeboxPatcher()

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

    async def _noop_refresh_collect(_self, _session: Session, *, reason: str) -> None:
        _ = reason
        return None

    def _fake_format(_self, gate: StageGateOutput, **_kwargs) -> str:
        return f"{gate.stage_id.value}:{gate.ready}:{gate.question}"

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._queue_constraint_extraction = _noop_queue_extract  # type: ignore[assignment]
    agent._decide_next_action = types.MethodType(_fake_decide, agent)
    agent._run_stage_gate = types.MethodType(_fake_stage_gate, agent)
    agent._refresh_collect_constraints_durable = types.MethodType(  # type: ignore[assignment]
        _noop_refresh_collect, agent
    )
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

    async def _noop_refresh_collect(_self, _session: Session, *, reason: str) -> None:
        _ = reason
        return None

    def _fake_format(_self, gate: StageGateOutput, **_kwargs) -> str:
        return f"STAGE={gate.stage_id.value}"

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._queue_constraint_extraction = _noop_queue_extract  # type: ignore[assignment]
    agent._decide_next_action = types.MethodType(_fake_decide, agent)
    agent._run_stage_gate = types.MethodType(_fake_stage_gate, agent)
    agent._refresh_collect_constraints_durable = types.MethodType(  # type: ignore[assignment]
        _noop_refresh_collect, agent
    )
    agent._collect_background_notes = lambda _session: None  # type: ignore[assignment]
    agent._format_stage_message = types.MethodType(_fake_format, agent)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.COLLECT_CONSTRAINTS
    session.stage_ready = True

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

    async def _noop_refresh_collect(_self, _session: Session, *, reason: str) -> None:
        _ = reason
        return None

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._queue_constraint_extraction = _noop_queue_extract  # type: ignore[assignment]
    agent._decide_next_action = types.MethodType(_fake_decide, agent)
    agent._refresh_collect_constraints_durable = types.MethodType(  # type: ignore[assignment]
        _noop_refresh_collect, agent
    )
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


@pytest.mark.asyncio
async def test_transition_routes_skeleton_edits_to_refine():
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
    session.stage = TimeboxingStage.SKELETON

    turn_init = types.SimpleNamespace(
        turn=types.SimpleNamespace(
            decision=StageDecision(action="provide_info"),
            user_text="move architecture to later and keep lunch exactly once",
        )
    )
    node = TransitionNode(orchestrator=agent, session=session, turn_init=turn_init)

    await node.on_messages([], cancellation_token=types.SimpleNamespace())

    assert session.stage == TimeboxingStage.REFINE
    assert node.stage_user_message == "move architecture to later and keep lunch exactly once"


@pytest.mark.asyncio
async def test_decision_node_honors_force_stage_rerun_override():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _should_not_decide(_self, _session: Session, *, user_message: str) -> StageDecision:
        _ = user_message
        raise AssertionError("decision LLM should be bypassed for stage-action reruns")

    agent._decide_next_action = types.MethodType(_should_not_decide, agent)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.force_stage_rerun = True
    turn_init = types.SimpleNamespace(turn=types.SimpleNamespace(user_text="", decision=None))
    node = DecisionNode(orchestrator=agent, session=session, turn_init=turn_init)

    response = await node.on_messages([], cancellation_token=types.SimpleNamespace())
    decision = response.chat_message.content

    assert isinstance(decision, StageDecision)
    assert decision.action == "redo"
    assert session.force_stage_rerun is False


@pytest.mark.asyncio
async def test_decision_node_forces_provide_info_when_stage_not_ready() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _fake_decide(
        _self, _session: Session, *, user_message: str
    ) -> StageDecision:
        assert "facet extraction" in user_message
        return StageDecision(action="proceed")

    agent._decide_next_action = types.MethodType(_fake_decide, agent)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.CAPTURE_INPUTS
    session.stage_ready = False
    turn_init = types.SimpleNamespace(
        turn=types.SimpleNamespace(
            user_text="DailyOneThing title is facet extraction with 2 blocks",
            decision=None,
        )
    )
    node = DecisionNode(orchestrator=agent, session=session, turn_init=turn_init)

    response = await node.on_messages([], cancellation_token=types.SimpleNamespace())
    decision = response.chat_message.content

    assert isinstance(decision, StageDecision)
    assert decision.action == "provide_info"
    assert decision.note == "stage_not_ready_user_detail"


@pytest.mark.asyncio
async def test_graphflow_assist_short_circuits_stage_execution():
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._timebox_patcher = TimeboxPatcher()

    async def _noop_calendar(_self, _session: Session, *, timeout_s: float = 0.0) -> None:
        return None

    def _noop_queue_extract(**_kwargs):
        return None

    async def _fake_decide(_self, _session: Session, *, user_message: str) -> StageDecision:
        assert user_message == "what pending tasks do I still have?"
        return StageDecision(action="assist", note="show pending tasks")

    async def _fake_assist(
        _self, *, session: Session, user_message: str, note: str | None
    ) -> str | None:
        assert session.stage == TimeboxingStage.CAPTURE_INPUTS
        assert "pending tasks" in user_message
        assert note == "show pending tasks"
        return "### Pending tasks (Task Marshal)\n- Task A"

    async def _should_not_run_stage_gate(*_args, **_kwargs):
        raise AssertionError("stage gate should not run for assist turns")

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._queue_constraint_extraction = _noop_queue_extract  # type: ignore[assignment]
    agent._decide_next_action = types.MethodType(_fake_decide, agent)
    agent._run_assist_turn = types.MethodType(_fake_assist, agent)
    agent._run_stage_gate = types.MethodType(_should_not_run_stage_gate, agent)
    agent._collect_background_notes = lambda _session: None  # type: ignore[assignment]
    agent._format_stage_message = lambda *_args, **_kwargs: "unused"  # type: ignore[assignment]

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.CAPTURE_INPUTS
    session.stage_ready = True

    flow = build_timeboxing_graphflow(orchestrator=agent, session=session)
    out: TextMessage | None = None
    async for item in flow.run_stream(
        task=TextMessage(content="what pending tasks do I still have?", source="user")
    ):
        if isinstance(item, TextMessage) and item.source == "PresenterNode":
            out = item
    assert out is not None
    assert out.content.startswith("### Pending tasks")
    assert session.skip_stage_execution is False


@pytest.mark.asyncio
async def test_stage_refine_proceed_without_edits_runs_patch_orchestration() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.REFINE
    session.timebox = object()
    session.tb_plan = object()

    async def _noop_calendar(_self, _session: Session, *, timeout_s: float = 0.0) -> None:
        _ = timeout_s
        return None

    def _no_issues(_self, _session: Session) -> RefinePreflight:
        return RefinePreflight()

    def _noop_materialize(_self, _session: Session) -> None:
        return None

    captured: dict[str, str] = {}

    async def _run_patch(
        _self,
        *,
        session: Session,
        patch_message: str,
        user_message: str,
    ) -> RefineToolExecutionOutcome:
        _ = session
        captured["patch_message"] = patch_message
        captured["user_message"] = user_message
        return RefineToolExecutionOutcome(
            patch_selected=True,
            memory_queued=True,
            fallback_patch_used=False,
            calendar=CalendarSyncOutcome(
                status="skipped",
                changed=False,
                note="No calendar changes in test fixture.",
            ),
            memory_operations=[],
        )

    async def _fake_summary(
        _self,
        *,
        stage: TimeboxingStage,
        timebox,
        session: Session | None = None,
    ) -> StageGateOutput:
        assert stage == TimeboxingStage.REFINE
        assert timebox is session.timebox
        return StageGateOutput(
            stage_id=TimeboxingStage.REFINE,
            ready=True,
            summary=["refine summary"],
            missing=[],
            question="Proceed to review?",
            facts={},
        )

    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar, agent)
    agent._ensure_refine_plan_state = types.MethodType(_no_issues, agent)
    agent._materialize_timebox_from_tb_plan = types.MethodType(_noop_materialize, agent)
    agent._run_refine_tool_orchestration = types.MethodType(_run_patch, agent)
    agent._compose_patcher_message = (
        lambda *, base_message, session, stage, extra: base_message  # type: ignore[assignment]
    )
    agent._quality_snapshot_for_prompt = lambda _session: {}  # type: ignore[assignment]
    agent._run_timebox_summary = types.MethodType(_fake_summary, agent)

    transition = types.SimpleNamespace(stage_user_message="")
    node = StageRefineNode(orchestrator=agent, session=session, transition=transition)

    response = await node.on_messages([], cancellation_token=types.SimpleNamespace())
    assert isinstance(response.chat_message.content, StageGateOutput)
    assert response.chat_message.content.stage_id == TimeboxingStage.REFINE
    assert session.stage_ready is True
    assert captured["patch_message"].startswith(
        "Prepare the editable Stage 4 plan from the current draft."
    )
    assert captured["user_message"].startswith(
        "Prepare the editable Stage 4 plan from the current draft."
    )
