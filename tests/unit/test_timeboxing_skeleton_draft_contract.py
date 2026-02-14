"""Tests for Stage 3 markdown-first skeleton drafting contract."""

from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.contracts import SkeletonContext
from fateforger.agents.timeboxing.tb_models import ET, FixedWindow, TBEvent, TBPlan


@pytest.mark.asyncio
async def test_run_skeleton_draft_uses_markdown_and_patcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 3 should draft markdown and build the plan through the patcher."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._timebox_patcher = SimpleNamespace()
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
    )
    captured: dict[str, Any] = {}

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

    async def _apply_patch(
        *,
        current: TBPlan,
        user_message: str,
        constraints: Any,
        actions: Any,
        plan_validator: Any | None = None,
    ) -> tuple[TBPlan, Any]:
        captured["current"] = current
        captured["user_message"] = user_message
        captured["constraints"] = constraints
        captured["actions"] = actions
        drafted = TBPlan(
            date=current.date,
            tz=current.tz,
            events=[
                TBEvent(
                    n="Deep Work",
                    t=ET.DW,
                    p=FixedWindow(st=time(10, 0), et=time(12, 0)),
                )
            ],
        )
        if plan_validator is not None:
            plan_validator(drafted)
        return drafted, {"ops": []}

    async def _passthrough_timeout(
        _label: str, awaitable: Any, *, timeout_s: float
    ) -> Any:
        _ = timeout_s
        return await awaitable

    monkeypatch.setattr(TimeboxingFlowAgent, "_ensure_stage_agents", _noop_stage_agents)
    monkeypatch.setattr(TimeboxingFlowAgent, "_run_skeleton_overview_markdown", _overview)
    monkeypatch.setattr(TimeboxingFlowAgent, "_collect_constraints", _constraints)
    monkeypatch.setattr(TimeboxingFlowAgent, "_build_skeleton_context", _context)
    monkeypatch.setattr(TimeboxingFlowAgent, "_build_skeleton_seed_plan", _seed)
    monkeypatch.setattr("fateforger.agents.timeboxing.agent.with_timeout", _passthrough_timeout)
    agent._timebox_patcher.apply_patch = _apply_patch  # type: ignore[attr-defined]

    drafted_timebox, markdown, drafted_plan = await TimeboxingFlowAgent._run_skeleton_draft(
        agent, session
    )

    assert markdown.startswith("## Day Overview")
    assert "Stage 3 markdown overview" in captured["user_message"]
    assert '"mode": "outline"' in captured["user_message"]
    assert drafted_timebox is None
    assert drafted_plan is not None
    assert drafted_plan.events[0].n == "Deep Work"
