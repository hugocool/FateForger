"""The skeleton: the context it is built from, how Stage 3 drafts it,
what happens when drafting times out, and Stage 2 pre-generation.
"""

from __future__ import annotations

import asyncio
import types
import pytest
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintSource,
    ConstraintStatus,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from datetime import date, time
from typing import Any
from fateforger.agents.timeboxing.contracts import SkeletonContext
from fateforger.agents.timeboxing.tb_models import ET, FixedWindow, TBEvent, TBPlan
from datetime import date
from autogen_ext.models.openai import OpenAIChatCompletionClient
from datetime import date, time, timedelta
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.nodes.nodes import StageSkeletonNode, TransitionNode
from fateforger.agents.timeboxing.timebox import Timebox


pytest.importorskip("autogen_agentchat")


# ── the skeleton context the coordinator builds ───────────────────────────────

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
    session.stage = TimeboxingStage.SKELETON  # constraints are scoped to active stage
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


# ── Stage 3: markdown first, seed plan, no patcher ────────────────────────────

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


# ── when drafting times out ───────────────────────────────────────────────────

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

    timebox, markdown, plan = await agent._run_skeleton_draft(session)

    assert timebox is None
    assert plan is not None
    assert plan.date == date(2026, 1, 21)
    assert plan.tz == "Europe/Amsterdam"
    assert len(plan.events) >= 1
    assert markdown.startswith("## Day Overview")
    assert any("deterministic fallback" in msg.lower() for msg in session.background_updates)


# ── Stage 2 pre-generation ────────────────────────────────────────────────────

async def test_stage_skeleton_uses_pre_generated_draft_without_llm() -> None:
    """Use ``session.pre_generated_skeleton`` and skip synchronous LLM drafting."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    pre_generated = Timebox(
        events=[
            CalendarEvent(
                summary="Focus Block",
                event_type=EventType.DEEP_WORK,
                start_time=time(9, 0),
                duration=timedelta(minutes=90),
            )
        ],
        date=date(2026, 2, 13),
        timezone="Europe/Amsterdam",
    )

    async def _should_not_run_draft(_session: Session) -> tuple[None, str, TBPlan | None]:
        raise AssertionError("Synchronous skeleton draft should not run.")
    agent._build_remote_snapshot_plan = types.MethodType(  # type: ignore[attr-defined]
        lambda self, _session: None,
        agent,
    )
    agent._render_markdown_summary_blocks = types.MethodType(  # type: ignore[attr-defined]
        lambda self, text: [],
        agent,
    )
    agent._run_skeleton_draft = types.MethodType(  # type: ignore[attr-defined]
        lambda self, session: _should_not_run_draft(session),
        agent,
    )

    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-13",
        tz_name="Europe/Amsterdam",
        frame_facts={"immovables": [{"title": "Meeting", "start": "10:00", "end": "11:00"}]},
        input_facts={"block_plan": {"deep_blocks": 2}},
    )
    session.pre_generated_skeleton = pre_generated
    session.pre_generated_skeleton_plan = TBPlan(
        date=date(2026, 2, 13),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Focus Block",
                t=ET.DW,
                p=FixedWindow(st=time(9, 0), et=time(10, 30)),
            )
        ],
    )
    session.pre_generated_skeleton_markdown = "## Day Overview\n- Focus Block"
    session.pre_generated_skeleton_fingerprint = (
        agent._skeleton_pregeneration_fingerprint(session)  # type: ignore[attr-defined]
    )

    transition = TransitionNode.__new__(TransitionNode)
    transition.stage_user_message = ""
    transition.decision = None

    node = StageSkeletonNode(
        orchestrator=agent,
        session=session,
        transition=transition,
    )
    await node.on_messages(
        [TextMessage(content="go", source="user")],
        CancellationToken(),
    )

    assert session.timebox is None
    assert session.tb_plan is not None
    assert session.base_snapshot is None
    assert session.skeleton_overview_markdown == "## Day Overview\n- Focus Block"
    assert session.stage_ready is True
    assert session.last_response == "Stage 3/5 (Skeleton)\nOverview ready below."
    assert session.pre_generated_skeleton is None
    assert session.pre_generated_skeleton_plan is None
    assert session.pre_generated_skeleton_markdown is None


@pytest.mark.asyncio
async def test_consume_pre_generated_skeleton_waits_for_inflight_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consume should await matching in-flight pre-generation before drafting sync."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t2",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
        frame_facts={"immovables": [{"title": "Meeting", "start": "10:00", "end": "11:00"}]},
        input_facts={"block_plan": {"deep_blocks": 2}},
    )
    expected_plan = TBPlan(
        date=date(2026, 2, 14),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Focus Block",
                t=ET.DW,
                p=FixedWindow(st=time(9, 0), et=time(10, 30)),
            )
        ],
    )
    session.pre_generated_skeleton_fingerprint = (
        agent._skeleton_pregeneration_fingerprint(session)  # type: ignore[attr-defined]
    )

    async def _background_complete() -> None:
        await asyncio.sleep(0.01)
        session.pre_generated_skeleton_plan = expected_plan
        session.pre_generated_skeleton_markdown = "## Day Overview\n- Focus Block"

    session.pre_generated_skeleton_task = asyncio.create_task(_background_complete())

    async def _should_not_run_sync_draft(
        self: TimeboxingFlowAgent, current: Session
    ) -> tuple[None, str, TBPlan | None]:
        _ = (self, current)
        raise AssertionError("Synchronous skeleton draft should not run.")

    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_run_skeleton_draft",
        _should_not_run_sync_draft,
    )

    _timebox, markdown, drafted_plan = await TimeboxingFlowAgent._consume_pre_generated_skeleton(
        agent, session
    )

    assert drafted_plan is expected_plan
    assert markdown == "## Day Overview\n- Focus Block"
