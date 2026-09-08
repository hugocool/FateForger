"""Unit tests for stage-decision fallback behavior."""

from __future__ import annotations

import pytest

from fateforger.agents.timeboxing import agent as timeboxing_agent_module
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


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
