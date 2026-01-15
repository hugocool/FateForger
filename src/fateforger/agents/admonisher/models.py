from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Enum as SAEnum, UniqueConstraint
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


class AdmonishmentSettings(Base):
    __tablename__ = "admonishment_settings"
    __table_args__ = (
        UniqueConstraint("scope_key", name="uq_admonishment_settings_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_delay_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    escalation: Mapped[str] = mapped_column(String, default="gentle", nullable=False)
    cancel_on_user_reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
