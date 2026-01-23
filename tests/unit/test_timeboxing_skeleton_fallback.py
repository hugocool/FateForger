"""Tests for skeleton draft fallback behavior."""

from __future__ import annotations

import asyncio
import types
from datetime import date, time, timedelta
from typing import Any

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.schedular.models.calendar import EventType
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient


class DummyDraftAgent:
    """Minimal draft agent stub for timeout fallback tests."""

    async def on_messages(self, *_args: Any, **_kwargs: Any) -> None:
        """Return no content because the timeout is injected."""
        return None


async def _noop_ensure_stage_agents(self: TimeboxingFlowAgent) -> None:
    """No-op stage agent initializer for testing."""
    return None


async def _noop_calendar_immovables(
    self: TimeboxingFlowAgent, _session: Session, *, timeout_s: float = 0.0
) -> None:
    """Skip calendar MCP fetch in unit tests."""
    return None


async def _timeout_with_timeout(
    _label: str, awaitable: Any, *, timeout_s: float
) -> None:
    """Raise a timeout to trigger the fallback path."""
    if hasattr(awaitable, "close"):
        awaitable.close()
    raise asyncio.TimeoutError


@pytest.mark.asyncio
async def test_skeleton_draft_timeout_fallback(monkeypatch) -> None:
    """Return a minimal timebox when skeleton drafting times out."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._draft_agent = DummyDraftAgent()
    agent._draft_model_client = OpenAIChatCompletionClient(model="gpt-4o-mini", api_key="test")
    agent._ensure_stage_agents = types.MethodType(_noop_ensure_stage_agents, agent)
    agent._constraint_store = None
    agent._ensure_calendar_immovables = types.MethodType(_noop_calendar_immovables, agent)

    monkeypatch.setattr(
        "fateforger.agents.timeboxing.agent.with_timeout", _timeout_with_timeout
    )

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-01-21",
        tz_name="Europe/Amsterdam",
    )

    timebox = await agent._run_skeleton_draft(session)

    assert timebox.date == date(2026, 1, 21)
    assert timebox.timezone == "Europe/Amsterdam"
    assert len(timebox.events) == 1
    event = timebox.events[0]
    assert event.summary == "Focus Block"
    assert event.event_type == EventType.DEEP_WORK
    assert event.start_time == time(9, 0)
    assert event.duration == timedelta(minutes=90)
    assert any("minimal skeleton" in msg.lower() for msg in session.background_updates)
