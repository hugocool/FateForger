from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class HauntTone(str, Enum):
    """Tone spectrum the haunt subsystem understands."""

    NEUTRAL = "neutral"
    ENCOURAGING = "encouraging"
    ASSERTIVE = "assertive"
    SUPPORTIVE = "supportive"
    PLAYFUL = "playful"


class HauntDirection(str, Enum):
    """Indicates whether we are logging an inbound or outbound message."""

    OUTBOUND = "outbound"
    INBOUND = "inbound"


class FollowUpPlan(BaseModel):
    """Directive describing how (and if) to follow up."""

    required: bool = Field(default=False, description="Whether a follow-up should be scheduled")
    delay_minutes: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minutes to wait before the first follow-up. Uses agent defaults when omitted.",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        description="Maximum number of follow-up attempts that should be made.",
    )


class HauntEnvelope(BaseModel):
    """Canonical Pydantic payload exchanged with the haunt orchestrator."""

    session_id: str = Field(..., description="Conversation/session identifier")
    agent_id: str = Field(..., description="Agent or haunter logical identifier")
    channel: str = Field(..., description="Logical channel (e.g. Slack DM id)")
    direction: HauntDirection = Field(..., description="Whether the message is inbound or outbound")
    content: str = Field(..., description="Raw text content of the message")
    core_intent: str = Field(
        ..., description="Short summary of what the user is trying to accomplish"
    )
    tone: HauntTone = Field(default=HauntTone.NEUTRAL, description="Voice to reuse when haunting")
    follow_up: FollowUpPlan = Field(
        default_factory=FollowUpPlan,
        description="Follow-up instructions supplied by the emitting agent",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured metadata (task ids, calendar references, etc)",
    )
    message_ref: Optional[str] = Field(
        default=None,
        description="External message reference (Slack ts, email id, etc.)",
    )
    attempt: int = Field(
        default=0,
        ge=0,
        description="Count of follow-up attempts already performed for this thread",
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp when the envelope was created",
    )


class HauntTicket(BaseModel):
    """Represents a scheduled follow-up managed by the orchestrator."""

    ticket_id: str = Field(..., description="Stable identifier used by APScheduler")
    session_id: str = Field(...)
    agent_id: str = Field(...)
    run_at: datetime = Field(..., description="When the follow-up will execute")
    attempt: int = Field(..., ge=0)
    payload: HauntEnvelope = Field(..., description="Original envelope that spawned the ticket")


class CalendarHook(BaseModel):
    """Timer request derived from a calendar commitment."""

    session_id: str = Field(...)
    agent_id: str = Field(...)
    event_id: str = Field(...)
    title: str = Field(...)
    start_at: datetime = Field(...)
    end_at: Optional[datetime] = Field(default=None)
    tone: HauntTone = Field(default=HauntTone.SUPPORTIVE)
    metadata: Dict[str, Any] = Field(default_factory=dict)

