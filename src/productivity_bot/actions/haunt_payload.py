"""
Haunt Payload data model for agent-to-agent communication.

This module defines the structured payload used for handoff between
Haunters and the PlanningAgent via the RouterAgent.
"""

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class HauntPayload(BaseModel):
    """
    Structured payload for haunter-to-planner communication.

    This model represents the data structure passed from Haunters
    through the RouterAgent to the PlanningAgent for calendar operations.
    """

    session_id: UUID = Field(description="Unique identifier for the planning session")
    action: Literal["create_event", "postpone", "mark_done", "commit_time"] = Field(
        description="The action to perform"
    )
    minutes: Optional[int] = Field(
        default=None,
        description="Minutes for postpone action (null for other actions)",
        ge=1,
        le=1440,  # Max 24 hours
    )
    commit_time_str: str = Field(description="Raw user text containing time commitment")

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "session_id": str(self.session_id),
            "action": self.action,
            "minutes": self.minutes,
            "commit_time_str": self.commit_time_str,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HauntPayload":
        """Create instance from dictionary."""
        return cls(
            session_id=UUID(data["session_id"]),
            action=data["action"],
            minutes=data.get("minutes"),
            commit_time_str=data["commit_time_str"],
        )

    def __str__(self) -> str:
        """String representation for logging."""
        return f"HauntPayload(session={self.session_id}, action={self.action})"
