"""Unit tests for stage-decision fallback behavior."""

from __future__ import annotations

import types

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
    agent._decision_agent = _DecisionAgentStub()

    async def _noop_ensure(self):
        _ = self
        return None

    async def _raise_timeout(_label, awaitable, *, timeout_s, **_kwargs):
        _ = timeout_s
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError("decision timeout")

    monkeypatch.setattr(
        timeboxing_agent_module, "with_timeout", _raise_timeout
    )
    agent._ensure_stage_agents = types.MethodType(_noop_ensure, agent)

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
    agent._decision_agent = _DecisionAgentStub()

    async def _noop_ensure(self):
        _ = self
        return None

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
    agent._ensure_stage_agents = types.MethodType(_noop_ensure, agent)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", committed=True)
    session.stage = TimeboxingStage.CAPTURE_INPUTS

    decision = await TimeboxingFlowAgent._decide_next_action(
        agent,
        session,
        user_message="continue",
    )

    assert decision.action == "provide_info"
    assert decision.note == "stage_decision_parse_error"
