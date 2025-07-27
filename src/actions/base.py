from pydantic import BaseModel, Field


class ActionBase(BaseModel):
    """Base for haunter actions."""

    action: str = Field(..., description="action name")
