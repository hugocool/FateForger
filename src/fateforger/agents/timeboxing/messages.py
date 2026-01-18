"""Typed messages for the timeboxing workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .actions import TimeboxAction
from .preferences import Constraint
from .timebox import Timebox


@dataclass
class StartTimeboxing:
    """Signal that a timeboxing session should begin inside a topic/thread."""

    channel_id: str
    thread_ts: str
    user_id: str
    user_input: str
    intent_summary: str | None = None
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TimeboxingCommitDate:
    """Stage 0: user commits the date (before constraints/calendar hydration)."""

    channel_id: str
    thread_ts: str
    user_id: str
    planned_date: str  # YYYY-MM-DD
    timezone: str  # IANA TZ name


@dataclass
class TimeboxingUserReply:
    """User feedback that should update the in-flight timeboxing session."""

    channel_id: str
    user_id: str
    text: str
    thread_ts: str


@dataclass
class TimeboxingFinalResult:
    """Final result emitted when a session completes or aborts."""

    thread_ts: str
    status: str
    summary: str
    payload: Optional[Dict[str, Any]] = None


@dataclass
class TimeboxPatchRecord:
    """Snapshot of a timebox modification and its justification."""

    created_at: datetime
    user_message: str
    constraint_ids: List[int] = field(default_factory=list)
    constraint_names: List[str] = field(default_factory=list)
    actions: List[TimeboxAction] = field(default_factory=list)


@dataclass
class TimeboxingUpdate:
    """Structured output for downstream consumers (constraints + timebox + patches)."""

    thread_ts: str
    channel_id: str
    user_id: str
    user_message: str
    constraints: List[Constraint] = field(default_factory=list)
    timebox: Optional[Timebox] = None
    actions: List[TimeboxAction] = field(default_factory=list)
    patch_history: List[TimeboxPatchRecord] = field(default_factory=list)


__all__ = [
    "StartTimeboxing",
    "TimeboxingCommitDate",
    "TimeboxingUserReply",
    "TimeboxingFinalResult",
    "TimeboxPatchRecord",
    "TimeboxingUpdate",
]
