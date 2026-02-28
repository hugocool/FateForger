"""Regression tests for per-session GraphFlow turn serialization."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.constants import TIMEBOXING_TIMEOUTS


class _ConcurrentUnsafeFlow:
    """Flow test double that raises if two run_stream calls overlap."""

    def __init__(self) -> None:
        self.running = 0
        self.max_running = 0
        self.calls = 0

    async def run_stream(self, task: TextMessage):  # type: ignore[override]
        self.calls += 1
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.running > 1:
                raise ValueError(
                    "The team is already running, it cannot run again until it is stopped."
                )
            await asyncio.sleep(0.03)
            yield TextMessage(content=f"ok:{task.content}", source="PresenterNode")
        finally:
            self.running -= 1


@pytest.mark.asyncio
async def test_run_graph_turn_serializes_concurrent_calls_per_session() -> None:
    """Concurrent user replies should not overlap GraphFlow.run_stream for one session."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._id = AgentId("timeboxing_agent", "test-key")
    agent._refresh_temporal_facts = lambda _session: None
    agent._session_debug = lambda *_args, **_kwargs: None

    flow = _ConcurrentUnsafeFlow()
    agent._ensure_graphflow = lambda _session: flow

    session = Session(
        thread_ts="thread-1",
        channel_id="C123",
        user_id="U123",
        committed=True,
        planned_date="2026-02-27",
    )

    first = TimeboxingFlowAgent._run_graph_turn(agent, session=session, user_text="a")
    second = TimeboxingFlowAgent._run_graph_turn(agent, session=session, user_text="b")
    out_a, out_b = await asyncio.gather(first, second)

    assert isinstance(out_a, TextMessage)
    assert isinstance(out_b, TextMessage)
    assert flow.calls == 2
    assert flow.max_running == 1


@pytest.mark.asyncio
async def test_run_graph_turn_returns_timeout_message_when_outer_timeout_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck graph turn should return a deterministic timeout reply instead of hanging."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._id = AgentId("timeboxing_agent", "test-key")
    agent._refresh_temporal_facts = lambda _session: None

    events: list[str] = []

    def _session_debug(_session: Session, event: str, **_payload: object) -> None:
        events.append(event)

    agent._session_debug = _session_debug

    class _SlowFlow:
        async def run_stream(self, task: TextMessage):  # type: ignore[override]
            await asyncio.sleep(0.2)
            yield TextMessage(content=f"late:{task.content}", source="PresenterNode")

    flow = _SlowFlow()
    agent._ensure_graphflow = lambda _session: flow

    monkeypatch.setattr(
        "fateforger.agents.timeboxing.agent.TIMEBOXING_TIMEOUTS",
        replace(TIMEBOXING_TIMEOUTS, graph_turn_s=0.01),
    )

    session = Session(
        thread_ts="thread-timeout",
        channel_id="C123",
        user_id="U123",
        committed=True,
        planned_date="2026-02-27",
    )

    out = await TimeboxingFlowAgent._run_graph_turn(
        agent, session=session, user_text="stuck"
    )

    assert isinstance(out, TextMessage)
    assert "processing timeout" in out.content
    assert events.count("graph_turn_timeout") == 1
    assert events.count("graph_turn_end") == 1
