from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Column, DateTime, Integer, String, Enum as SAEnum
from sqlalchemy.orm import declarative_base, Mapped, mapped_column

Base = declarative_base()

class SessionStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"

class PlanningSession(Base):
    __tablename__ = "planning_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus), default=SessionStatus.IN_PROGRESS, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

class SlackMessage(Base):
    __tablename__ = "slack_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_id: Mapped[str | None] = mapped_column(String, nullable=True)
