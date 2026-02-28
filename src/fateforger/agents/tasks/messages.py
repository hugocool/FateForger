"""Typed inter-agent contracts for task-marshalling snapshots."""

from __future__ import annotations

from enum import Enum

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
]
