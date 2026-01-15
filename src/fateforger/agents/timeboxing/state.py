"""Persistent state model for timeboxing sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Stage(str, Enum):
    HYDRATE = "hydrate"
    DRAFT = "draft"
    CRITIQUE = "critique"
    PATCH = "patch"
    AWAIT_APPROVAL = "await_approval"
    SUBMIT = "submit"
    DONE = "done"
    ABORT = "abort"


@dataclass
class TimeboxingState:
    """Serializable state for a timeboxing planning session."""

    topic_id: str
    stage: Stage = Stage.HYDRATE
    inputs: Dict[str, Any] = field(default_factory=dict)
    todos: List[Dict[str, Any]] = field(default_factory=list)
    draft_json: Optional[Dict[str, Any]] = None
    quality: float = 0.0
    awaiting_user_approval: bool = False
    approval_message_ts: Optional[str] = None
    submit_result: Optional[Dict[str, Any]] = None
    history: List[str] = field(default_factory=list)

    def record(self, entry: str) -> None:
        self.history.append(entry)

    def min_inputs_ready(self) -> bool:
        return bool(
            self.inputs.get("work_window")
            and (self.todos or self.inputs.get("goal"))
        )

    def meets_quality(self, threshold: float = 0.8) -> bool:
        return self.quality >= threshold


__all__ = ["Stage", "TimeboxingState"]

