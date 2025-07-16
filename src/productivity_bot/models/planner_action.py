"""
Pydantic models for structured planner action parsing.

This module defines the schema for allowed user intents in planning sessions,
enabling constrained LLM generation with OpenAI's Structured Outputs.
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class PlannerAction(BaseModel):
    """
    Structured representation of user intents in planning sessions.
    
    This model constrains the LLM to only produce valid actions with
    properly typed parameters, eliminating the need for regex parsing.
    """
    
    action: Literal["postpone", "mark_done", "recreate_event"] = Field(
        ..., description="Type of user intent"
    )
    minutes: Optional[int] = Field(
        None,
        description="Number of minutes to postpone; required if action=='postpone'"
    )
    
    def __str__(self) -> str:
        """String representation for logging."""
        if self.action == "postpone":
            return f"PlannerAction(action={self.action}, minutes={self.minutes})"
        return f"PlannerAction(action={self.action})"
    
    @property
    def is_postpone(self) -> bool:
        """Check if this is a postpone action."""
        return self.action == "postpone"
    
    @property
    def is_mark_done(self) -> bool:
        """Check if this is a mark done action."""
        return self.action == "mark_done"
    
    @property
    def is_recreate_event(self) -> bool:
        """Check if this is a recreate event action."""
        return self.action == "recreate_event"
    
    def get_postpone_minutes(self, default: int = 15) -> int:
        """
        Get postpone minutes with fallback default.
        
        Args:
            default: Default minutes if none specified
            
        Returns:
            Number of minutes to postpone
        """
        if self.action == "postpone":
            return self.minutes or default
        return 0
