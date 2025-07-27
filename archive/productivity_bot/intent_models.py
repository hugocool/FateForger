from enum import Enum
from typing import Optional, Literal
from pydantic import BaseModel, Field

class Intent(str, Enum):
    POSTPONE = "postpone"
    MARK_DONE = "mark_done"
    CREATE_EVENT = "create_event"
    COMMIT_TIME = "commit_time"
    UNKNOWN = "unknown"

class PlannerAction(BaseModel, strict=True):
    """Unified intent payload emitted by the planner agents."""

    action: Literal[
        Intent.POSTPONE,
        Intent.MARK_DONE,
        Intent.CREATE_EVENT,
        Intent.COMMIT_TIME,
        Intent.UNKNOWN,
    ] = Field(..., description="Which planner action the user requested")

    minutes: Optional[int] = Field(
        default=None,
        ge=1,
        le=1440,
        description="Delay in minutes if action==postpone",
    )

    commitment_time: Optional[str] = Field(
        default=None,
        description="Natural-language time like '8 pm tomorrow'",
    )

    @property
    def is_postpone(self) -> bool:
        return self.action == Intent.POSTPONE

    @property
    def is_mark_done(self) -> bool:
        return self.action == Intent.MARK_DONE

    @property
    def is_create_event(self) -> bool:
        return self.action == Intent.CREATE_EVENT

    @property
    def is_commit_time(self) -> bool:
        return self.action == Intent.COMMIT_TIME

    @property
    def is_unknown(self) -> bool:
        return self.action == Intent.UNKNOWN

    def get_postpone_minutes(self) -> Optional[int]:
        if self.is_postpone:
            return self.minutes or 15
        return None
