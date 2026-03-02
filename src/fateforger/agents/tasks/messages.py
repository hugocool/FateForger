"""Typed inter-agent contracts for task-marshalling snapshots."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PendingTaskSnapshotRequest(BaseModel):
    """Request a bounded snapshot of pending tasks for planning assistance."""

    user_id: str
    limit: int = Field(default=12, ge=1, le=50)
    per_project_limit: int = Field(default=4, ge=1, le=20)
    query: str | None = None


class PendingTaskItem(BaseModel):
    """Minimal pending-task row used by other agents."""

    id: str
    title: str
    project_id: str | None = None
    project_name: str | None = None


class PendingTaskSnapshot(BaseModel):
    """Typed response carrying pending-task candidates."""

    items: list[PendingTaskItem] = Field(default_factory=list)
    summary: str = ""


class GuidedRefinementPhase(str, Enum):
    """Phase IDs for guided task refinement sessions."""

    SCOPE = "scope"
    SCAN = "scan"
    REFINE = "refine"
    CLOSE = "close"


class GuidedRefinedWorkItem(BaseModel):
    """Refined work item shape emitted by the guided session."""

    title: str
    project_area: str
    state: str = Field(
        description="One of: hypothesis, research, engineering, blocked."
    )
    acceptance_criteria: list[str] = Field(default_factory=list)
    definition_of_done_binary: str
    size: str = Field(description="Expected size bucket, e.g. XS/S/M/L.")
    blocked_by: list[str] = Field(default_factory=list)
    next_action: str


class GuidedRefinementRecap(BaseModel):
    """Structured recap for cross-agent consumption."""

    focus_areas: list[str] = Field(default_factory=list)
    refined_items: list[GuidedRefinedWorkItem] = Field(default_factory=list)
    stuck_or_postponed_signals: list[str] = Field(default_factory=list)
    user_intention: str = ""
    summary: str = ""


class GuidedRefinementTurn(BaseModel):
    """Structured assistant output for one guided session turn."""

    phase: GuidedRefinementPhase
    gate_met: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    phase_summary: list[str] = Field(default_factory=list)
    assistant_message: str
    recap: GuidedRefinementRecap | None = None
    session_complete: bool = False


class GuidedRefinementRecapRequest(BaseModel):
    """Request the most recent guided-refinement recap for a user."""

    user_id: str


class GuidedRefinementRecapResponse(BaseModel):
    """Response carrying the latest guided-refinement recap, if any."""

    found: bool = False
    recap: GuidedRefinementRecap | None = None


class TaskDueActionRequest(BaseModel):
    """Action request routed from Slack buttons to tasks_agent."""

    action: Literal["view_all_due"] = "view_all_due"
    user_id: str
    due_date: str
    source: str = "ticktick"
    channel_id: str = ""
    thread_ts: str = ""
    ticktick_project_ids: list[str] = Field(default_factory=list)


class TaskDetailsModalRequest(BaseModel):
    """Request to build the task details/edit modal payload."""

    user_id: str
    channel_id: str
    thread_ts: str
    task_id: str
    project_id: str
    label: str
    title: str
    project_name: str
    due_date: str = ""


class TaskDetailsModalResponse(BaseModel):
    """Response carrying a fully-built Slack modal view payload."""

    ok: bool = False
    error: str = ""
    view: dict[str, Any] | None = None


class TaskEditTitleRequest(BaseModel):
    """Request to update one task title via modal or NL flows."""

    user_id: str
    channel_id: str
    thread_ts: str
    task_id: str
    project_id: str
    label: str
    new_title: str


class TaskEditTitleResponse(BaseModel):
    """Response from task title patching operations."""

    ok: bool = False
    message: str = ""


__all__ = [
    "PendingTaskSnapshotRequest",
    "PendingTaskItem",
    "PendingTaskSnapshot",
    "GuidedRefinementPhase",
    "GuidedRefinedWorkItem",
    "GuidedRefinementRecap",
    "GuidedRefinementTurn",
    "GuidedRefinementRecapRequest",
    "GuidedRefinementRecapResponse",
    "TaskDueActionRequest",
    "TaskDetailsModalRequest",
    "TaskDetailsModalResponse",
    "TaskEditTitleRequest",
    "TaskEditTitleResponse",
]
