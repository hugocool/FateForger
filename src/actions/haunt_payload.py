from typing import Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field

class HauntPayload(BaseModel):
    """Structured payload sent from haunters to PlanningAgent."""

    session_id: Union[int, UUID] = Field(..., description="planning session id")
    action: Literal["create_event", "postpone", "mark_done", "unknown"]
    minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    commit_time_str: Optional[str] = None
