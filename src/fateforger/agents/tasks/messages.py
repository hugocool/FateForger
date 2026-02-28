"""Typed inter-agent contracts for task-marshalling snapshots."""

from __future__ import annotations

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


__all__ = [
    "PendingTaskSnapshotRequest",
    "PendingTaskItem",
    "PendingTaskSnapshot",
]
