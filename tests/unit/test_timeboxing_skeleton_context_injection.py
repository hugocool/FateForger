"""Unit tests for explicit SkeletonContext injection."""

from __future__ import annotations

import asyncio
import types

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintSource,
    ConstraintStatus,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


async def _noop_ensure_calendar(self, _session, *, timeout_s=0.0) -> None:
    """Disable MCP calendar fetch for unit tests."""
    return None


@pytest.mark.asyncio
async def test_build_skeleton_context_includes_constraints_and_immovables() -> None:
    """Ensure the coordinator injects constraints + immovables into SkeletonContext."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_store = None
    agent._ensure_calendar_immovables = types.MethodType(_noop_ensure_calendar, agent)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.planned_date = "2026-01-21"
    session.tz_name = "Europe/Amsterdam"
    session.frame_facts = {
        "immovables": [{"title": "Gym", "start": "18:00", "end": "19:30"}]
    }
    session.durable_constraints_by_stage[TimeboxingStage.SKELETON.value] = [
        Constraint(
            name="No calls after 17:00",
            description="Avoid meetings after 17:00",
            necessity=ConstraintNecessity.MUST,
            user_id="u1",
            status=ConstraintStatus.PROPOSED,
            source=ConstraintSource.USER,
        )
    ]

    ctx = await agent._build_skeleton_context(session)
    assert ctx.timezone == "Europe/Amsterdam"
    assert ctx.immovables and ctx.immovables[0].title == "Gym"
    assert ctx.constraints_snapshot
    assert ctx.constraints_snapshot[0].name == "No calls after 17:00"
