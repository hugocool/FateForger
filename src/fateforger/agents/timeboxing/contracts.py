"""Typed stage context contracts for the timeboxing coordinator and LLM stages."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from fateforger.agents.timeboxing.preferences import Constraint


class WorkWindow(BaseModel):
    """Canonical work window for a planning day."""

    start: str = Field(..., description="HH:MM")
    end: str = Field(..., description="HH:MM")


class SleepTarget(BaseModel):
    """Optional sleep target for a planning day."""

    start: Optional[str] = Field(default=None, description="HH:MM")
    end: Optional[str] = Field(default=None, description="HH:MM")
    hours: Optional[float] = Field(default=None)


class Immovable(BaseModel):
    """Normalized calendar immovable event for timeboxing."""

    title: str
    start: str = Field(..., description="HH:MM")
    end: str = Field(..., description="HH:MM")


class BlockPlan(BaseModel):
    """Block-based planning settings for the day."""

    deep_blocks: Optional[int] = None
    shallow_blocks: Optional[int] = None
    block_minutes: Optional[int] = None
    focus_theme: Optional[str] = None


class TaskCandidate(BaseModel):
    """Candidate task input for block assignment."""

    title: str
    block_count: Optional[int] = None
    duration_min: Optional[int] = None
    due: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    importance: Optional[Literal["high", "med", "low"]] = None


class DailyOneThing(BaseModel):
    """Daily One Thing for block allocation."""

    title: str
    block_count: Optional[int] = None
    duration_min: Optional[int] = None


class CollectConstraintsContext(BaseModel):
    """Input contract for Stage 1 (CollectConstraints) gating agent."""

    stage_id: Literal["CollectConstraints"] = "CollectConstraints"
    user_message: str
    facts: Dict[str, Any] = Field(default_factory=dict)
    immovables: List[Immovable] = Field(default_factory=list)
    durable_constraints: List[Constraint] = Field(default_factory=list)


class CaptureInputsContext(BaseModel):
    """Input contract for Stage 2 (CaptureInputs) gating agent."""

    stage_id: Literal["CaptureInputs"] = "CaptureInputs"
    user_message: str
    frame_facts: Dict[str, Any] = Field(default_factory=dict)
    input_facts: Dict[str, Any] = Field(default_factory=dict)


class SkeletonContext(BaseModel):
    """Input contract for Stage 3 (Skeleton) draft agent."""

    stage_id: Literal["Skeleton"] = "Skeleton"
    date: date
    timezone: str
    work_window: Optional[WorkWindow] = None
    sleep_target: Optional[SleepTarget] = None
    immovables: List[Immovable] = Field(default_factory=list)
    block_plan: Optional[BlockPlan] = None
    daily_one_thing: Optional[DailyOneThing] = None
    tasks: List[TaskCandidate] = Field(default_factory=list)
    constraints_snapshot: List[Constraint] = Field(default_factory=list)


__all__ = [
    "BlockPlan",
    "CaptureInputsContext",
    "CollectConstraintsContext",
    "DailyOneThing",
    "Immovable",
    "SkeletonContext",
    "SleepTarget",
    "TaskCandidate",
    "WorkWindow",
]
