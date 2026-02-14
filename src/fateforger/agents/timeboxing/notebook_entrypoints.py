"""Notebook-oriented entrypoints for inspecting and exercising timeboxing flow.

These helpers are intentionally thin wrappers around the real agent methods.
They are designed for interactive notebook use without duplicating core logic.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

from .agent import Session, TimeboxingFlowAgent
from .stage_gating import TimeboxingStage
from .tb_models import TBPlan


@dataclass(frozen=True)
class MethodLocation:
    """Source location for a Stage 3-relevant method."""

    name: str
    file_path: str
    line: int


@dataclass
class Stage3DraftTrace:
    """Result of running Stage 3 drafting directly."""

    markdown: str
    tb_plan: TBPlan | None
    stage: TimeboxingStage
    stage_ready: bool
    stage_missing: list[str]
    stage_question: str | None
    debug_log_path: str | None


@dataclass
class GraphTurnTrace:
    """Result of running one GraphFlow turn."""

    user_text: str
    response_text: str
    stage: TimeboxingStage
    stage_ready: bool
    stage_missing: list[str]
    stage_question: str | None
    skeleton_markdown: str | None
    tb_plan: TBPlan | None
    debug_log_path: str | None


def create_agent(*, name: str = "timeboxing_notebook") -> TimeboxingFlowAgent:
    """Instantiate the real timeboxing agent for notebook use."""
    return TimeboxingFlowAgent(name=name)


def create_session(
    *,
    channel_id: str = "notebook",
    user_id: str = "notebook-user",
    thread_ts: str | None = None,
    planned_date: str | None = None,
    tz_name: str = "Europe/Amsterdam",
    stage: TimeboxingStage = TimeboxingStage.SKELETON,
) -> Session:
    """Create a session object suitable for direct Stage 3 experiments."""
    if thread_ts is None:
        thread_ts = f"{int(time.time())}.{uuid4().hex[:8]}"
    if planned_date is None:
        planned_date = date.today().isoformat()
    return Session(
        thread_ts=thread_ts,
        channel_id=channel_id,
        user_id=user_id,
        planned_date=planned_date,
        tz_name=tz_name,
        stage=stage,
        session_key=f"{channel_id}:{thread_ts}",
    )


def stage3_method_locations() -> dict[str, MethodLocation]:
    """Return source locations for core Stage 3 implementation entrypoints."""
    from .nodes.nodes import StageSkeletonNode

    methods: dict[str, Any] = {
        "agent._run_skeleton_draft": TimeboxingFlowAgent._run_skeleton_draft,
        "agent._run_skeleton_overview_markdown": TimeboxingFlowAgent._run_skeleton_overview_markdown,
        "agent._build_skeleton_seed_plan": TimeboxingFlowAgent._build_skeleton_seed_plan,
        "agent._consume_pre_generated_skeleton": TimeboxingFlowAgent._consume_pre_generated_skeleton,
        "node.StageSkeletonNode.on_messages": StageSkeletonNode.on_messages,
    }
    out: dict[str, MethodLocation] = {}
    for name, fn in methods.items():
        file_path = inspect.getsourcefile(fn) or "<unknown>"
        _, line = inspect.getsourcelines(fn)
        out[name] = MethodLocation(name=name, file_path=file_path, line=line)
    return out


def stage3_framework_report() -> dict[str, bool]:
    """Report whether Stage 3 uses framework-native building blocks."""
    from .nodes.nodes import StageSkeletonNode

    draft_src = inspect.getsource(TimeboxingFlowAgent._run_skeleton_draft)
    overview_src = inspect.getsource(TimeboxingFlowAgent._run_skeleton_overview_markdown)
    node_src = inspect.getsource(StageSkeletonNode.on_messages)

    return {
        "uses_autogen_assistant_for_markdown": "AssistantAgent(" in overview_src,
        "uses_patcher_for_plan_draft": "_timebox_patcher.apply_patch(" in draft_src,
        "stage3_presentation_first_node": "self._session.timebox = None" in node_src,
        "stage3_slack_markdown_block_path": "_render_markdown_summary_blocks" in node_src,
        "stage3_has_no_direct_timebox_validator_in_draft_call": "plan_validator=" not in draft_src,
    }


def stage3_source_snippets() -> dict[str, str]:
    """Return source snippets for direct notebook inspection."""
    from .nodes.nodes import StageSkeletonNode

    return {
        "agent._run_skeleton_draft": inspect.getsource(TimeboxingFlowAgent._run_skeleton_draft),
        "agent._run_skeleton_overview_markdown": inspect.getsource(
            TimeboxingFlowAgent._run_skeleton_overview_markdown
        ),
        "agent._build_skeleton_seed_plan": inspect.getsource(
            TimeboxingFlowAgent._build_skeleton_seed_plan
        ),
        "node.StageSkeletonNode.on_messages": inspect.getsource(
            StageSkeletonNode.on_messages
        ),
    }


async def run_stage3_draft(
    *,
    agent: TimeboxingFlowAgent,
    session: Session,
) -> Stage3DraftTrace:
    """Run Stage 3 draft path directly (no duplicated stage logic)."""
    _, markdown, tb_plan = await agent._run_skeleton_draft(session)
    return Stage3DraftTrace(
        markdown=markdown,
        tb_plan=tb_plan,
        stage=session.stage,
        stage_ready=session.stage_ready,
        stage_missing=list(session.stage_missing or []),
        stage_question=session.stage_question,
        debug_log_path=session.debug_log_path,
    )


async def run_graph_turn(
    *,
    agent: TimeboxingFlowAgent,
    session: Session,
    user_text: str,
) -> GraphTurnTrace:
    """Run one real GraphFlow turn and return structured trace data."""
    reply = await agent._run_graph_turn(session=session, user_text=user_text)
    return GraphTurnTrace(
        user_text=user_text,
        response_text=reply.content,
        stage=session.stage,
        stage_ready=session.stage_ready,
        stage_missing=list(session.stage_missing or []),
        stage_question=session.stage_question,
        skeleton_markdown=session.skeleton_overview_markdown,
        tb_plan=session.tb_plan,
        debug_log_path=session.debug_log_path,
    )


__all__ = [
    "GraphTurnTrace",
    "MethodLocation",
    "Stage3DraftTrace",
    "create_agent",
    "create_session",
    "run_graph_turn",
    "run_stage3_draft",
    "stage3_framework_report",
    "stage3_method_locations",
    "stage3_source_snippets",
]

