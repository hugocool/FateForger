"""How a stage decides what comes next: the decision node, the fallback
when the gate cannot answer, and the JSON context the gate is given.
"""

from __future__ import annotations

from types import SimpleNamespace
import pytest
from fateforger.agents.timeboxing import agent as timeboxing_agent_module
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from dataclasses import dataclass
from typing import Any


pytest.importorskip("autogen_agentchat")


# ── the decision node ─────────────────────────────────────────────────────────

from autogen_core import CancellationToken

from fateforger.agents.timeboxing.agent import Session
from fateforger.agents.timeboxing.nodes.nodes import DecisionNode, TurnContext


class _OrchestratorStub:
    async def _decide_next_action(self, *_args, **_kwargs):
        raise AssertionError("Decision LLM path should not run when force rerun is set.")


@pytest.mark.asyncio
async def test_decision_node_respects_force_stage_rerun_flag() -> None:
    """DecisionNode should consume `force_stage_rerun` without touching LLM routing."""
    session = Session(thread_ts="T1", channel_id="C1", user_id="U1")
    session.force_stage_rerun = True
    turn_init = SimpleNamespace(turn=TurnContext(user_text="Proceed."))
    node = DecisionNode(
        orchestrator=_OrchestratorStub(),
        session=session,
        turn_init=turn_init,
    )

    await node.on_messages([], CancellationToken())

    assert turn_init.turn.decision is not None
    assert turn_init.turn.decision.action == "redo"
    assert turn_init.turn.decision.note == "stage_action_rerun"
    assert session.force_stage_rerun is False


# ── falling back when the gate cannot decide ──────────────────────────────────

class _DecisionAgentStub:
    async def on_messages(self, *_args, **_kwargs):
        return object()


@pytest.mark.asyncio
async def test_decide_next_action_timeout_returns_provide_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    # _build_one_shot_agent is called per-turn; point it at a stub instead of a real LLM.
    agent._build_one_shot_agent = lambda *_a, **_kw: _DecisionAgentStub()
    agent._session_debug_loggers = {}

    async def _raise_timeout(_label, awaitable, *, timeout_s, **_kwargs):
        _ = timeout_s
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError("decision timeout")

    monkeypatch.setattr(timeboxing_agent_module, "with_timeout", _raise_timeout)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.CAPTURE_INPUTS

    decision = await TimeboxingFlowAgent._decide_next_action(
        agent,
        session,
        user_message="continue",
    )

    assert decision.action == "provide_info"
    assert decision.note == "stage_decision_timeout"


@pytest.mark.asyncio
async def test_decide_next_action_parse_error_returns_provide_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    # _build_one_shot_agent is called per-turn; point it at a stub instead of a real LLM.
    agent._build_one_shot_agent = lambda *_a, **_kw: _DecisionAgentStub()
    agent._session_debug_loggers = {}

    async def _return_dummy(_label, awaitable, *, timeout_s, **_kwargs):
        _ = timeout_s
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        return object()

    def _raise_parse(*_args, **_kwargs):
        raise ValueError("bad parse")

    monkeypatch.setattr(timeboxing_agent_module, "with_timeout", _return_dummy)
    monkeypatch.setattr(timeboxing_agent_module, "parse_chat_content", _raise_parse)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.CAPTURE_INPUTS

    decision = await TimeboxingFlowAgent._decide_next_action(
        agent,
        session,
        user_message="continue",
    )

    assert decision.action == "provide_info"
    assert decision.note == "stage_decision_parse_error"


# ── the JSON context the gate reads ───────────────────────────────────────────

from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import StageGateOutput, TimeboxingStage


@dataclass
class _DummyChatMessage:
    content: Any


@dataclass
class _DummyResponse:
    chat_message: _DummyChatMessage


class _CapturingStageAgent:
    """Fake stage agent that captures incoming messages and returns a fixed output."""

    def __init__(self) -> None:
        self.last_messages: list[TextMessage] = []

    async def on_messages(
        self, messages: list[TextMessage], _token: Any
    ) -> _DummyResponse:
        """Capture messages and return a minimal StageGateOutput."""
        self.last_messages = messages
        return _DummyResponse(
            chat_message=_DummyChatMessage(
                content=StageGateOutput(
                    stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
                    ready=False,
                    summary=["ok"],
                    missing=["x"],
                    question="q",
                    facts={},
                )
            )
        )


class _MalformedStageAgent:
    """Fake stage agent returning malformed payload to test fallback behavior."""

    async def on_messages(
        self, messages: list[TextMessage], _token: Any
    ) -> _DummyResponse:
        _ = messages
        return _DummyResponse(chat_message=_DummyChatMessage(content="not-json"))


@pytest.mark.asyncio
async def test_run_stage_gate_sends_strict_json_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensures `_run_stage_gate` injects list-shaped data via TOON tables."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _noop_ensure_stage_agents(self: TimeboxingFlowAgent) -> None:
        """Avoid building real LLM agents in this unit test."""
        return None

    monkeypatch.setattr(
        TimeboxingFlowAgent, "_ensure_stage_agents", _noop_ensure_stage_agents
    )

    capturing = _CapturingStageAgent()
    # _build_one_shot_agent is called per invocation — return our capturing stub.
    agent._build_one_shot_agent = lambda *_a, **_kw: capturing
    agent._constraint_search_tool = None  # accessed directly in _run_stage_gate

    context = {
        "stage_id": "CollectConstraints",
        "user_message": "hi",
        "facts": {"k": 1},
        "durable_constraints": [
            {
                "name": "Sleep target",
                "description": "Aim for 8 hours",
                "necessity": "should",
                "status": "proposed",
                "source": "system",
                "scope": "profile",
                "tags": [],
                "hints": {},
            }
        ],
    }
    out = await TimeboxingFlowAgent._run_stage_gate(  # type: ignore[misc]
        agent,
        stage=TimeboxingStage.COLLECT_CONSTRAINTS,
        user_message="hi",
        context=context,
    )

    assert out.stage_id == TimeboxingStage.COLLECT_CONSTRAINTS
    assert capturing.last_messages, "Expected a single JSON message to be sent"
    content = capturing.last_messages[0].content
    assert "TOON format" in content
    assert "facts_json:" in content
    assert '"k": 1' in content
    assert "immovables[0]{title,start,end}:" in content
    assert (
        "durable_constraints[1]{name,necessity,scope,status,source,description}:"
        in content
    )


@pytest.mark.asyncio
async def test_run_stage_gate_returns_safe_fallback_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed model output should not crash stage execution."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _noop_ensure_stage_agents(self: TimeboxingFlowAgent) -> None:
        return None

    monkeypatch.setattr(
        TimeboxingFlowAgent, "_ensure_stage_agents", _noop_ensure_stage_agents
    )
    # _build_one_shot_agent is called per invocation — return our malformed stub.
    agent._build_one_shot_agent = lambda *_a, **_kw: _MalformedStageAgent()
    agent._constraint_search_tool = None  # accessed directly in _run_stage_gate

    context = {"facts": {"timezone": "Europe/Amsterdam"}}
    out = await TimeboxingFlowAgent._run_stage_gate(  # type: ignore[misc]
        agent,
        stage=TimeboxingStage.COLLECT_CONSTRAINTS,
        user_message="use defaults",
        context=context,
    )

    assert out.stage_id == TimeboxingStage.COLLECT_CONSTRAINTS
    assert out.ready is False
    assert "stage retry required" in out.missing
    assert "_stage_gate_error" in out.facts
    assert out.facts["timezone"] == "Europe/Amsterdam"
