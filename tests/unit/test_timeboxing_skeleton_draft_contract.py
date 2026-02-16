"""Tests for Stage 3 markdown-first skeleton drafting contract."""

from __future__ import annotations

import types
from datetime import date, time
from typing import Any

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.contracts import SkeletonContext
from fateforger.agents.timeboxing.tb_models import ET, FixedWindow, TBEvent, TBPlan


@pytest.mark.asyncio
async def test_run_skeleton_draft_uses_markdown_and_seed_plan_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 3 should draft markdown and carry a seed plan without patching."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
    )
    agent._timebox_patcher = types.SimpleNamespace(
        apply_patch=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Stage 3 must not call patcher.")
        )
    )

    async def _noop_stage_agents(self: TimeboxingFlowAgent) -> None:
        return None

    async def _overview(
        self: TimeboxingFlowAgent, *, context: Any
    ) -> str:
        _ = context
        return "## Day Overview\n### Morning\n- Deep Work (120 min)"

    async def _constraints(self: TimeboxingFlowAgent, _session: Session) -> list[Any]:
        return []

    async def _context(
        self: TimeboxingFlowAgent, _session: Session
    ) -> SkeletonContext:
        return SkeletonContext(
            date=date(2026, 2, 14),
            timezone="Europe/Amsterdam",
        )

    def _seed(self: TimeboxingFlowAgent, _session: Session) -> TBPlan:
        return TBPlan(
            date=date(2026, 2, 14),
            tz="Europe/Amsterdam",
            events=[
                TBEvent(
                    n="Anchor",
                    t=ET.M,
                    p=FixedWindow(st=time(9, 0), et=time(10, 0)),
                )
            ],
        )

    monkeypatch.setattr(TimeboxingFlowAgent, "_ensure_stage_agents", _noop_stage_agents)
    monkeypatch.setattr(TimeboxingFlowAgent, "_run_skeleton_overview_markdown", _overview)
    monkeypatch.setattr(TimeboxingFlowAgent, "_build_skeleton_context", _context)
    monkeypatch.setattr(TimeboxingFlowAgent, "_build_skeleton_seed_plan", _seed)
    monkeypatch.setattr(TimeboxingFlowAgent, "_collect_constraints", _constraints)

    drafted_timebox, markdown, drafted_plan = await TimeboxingFlowAgent._run_skeleton_draft(
        agent, session
    )

    assert markdown.startswith("## Day Overview")
    assert drafted_timebox is None
    assert drafted_plan is not None
    assert drafted_plan.events[0].n == "Anchor"
