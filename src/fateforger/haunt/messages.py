from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, Optional

FollowUpEscalation = Literal["gentle", "firm", "menacing"]


@dataclass(frozen=True)
class FollowUpSpec:
    should_schedule: bool
    after: Optional[timedelta] = None
    max_attempts: int | None = None
    escalation: FollowUpEscalation | None = None
    cancel_on_user_reply: bool | None = None


@dataclass(frozen=True)
class UserFacingMessage:
    content: str
    followup: Optional[FollowUpSpec] = None
    task_id: Optional[str] = None
    user_id: Optional[str] = None
    channel_id: Optional[str] = None


@dataclass(frozen=True)
class FollowUpDue:
    message_id: str
    topic_id: str | None
    task_id: str | None
    attempt: int
    escalation: FollowUpEscalation
    user_id: str | None


__all__ = [
    "FollowUpSpec",
    "FollowUpEscalation",
    "UserFacingMessage",
    "FollowUpDue",
]
