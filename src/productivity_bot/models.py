"""
Database models for the productivity bot.
"""

from datetime import datetime, date as DateType
from enum import Enum as PyEnum
from typing import Optional, List
from sqlalchemy import String, Integer, DateTime, Date, Enum, Text, Boolean, ForeignKey
from sqlalchemy.orm import mapped_column, relationship, Mapped
from .common import Base


class PlanStatus(PyEnum):
    """Status of a planning session."""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class PlanningSession(Base):
    """Tracks the state of planning sessions for users."""
    
    __tablename__ = "planning_sessions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    date: Mapped[DateType] = mapped_column(Date, index=True, nullable=False)
    status: Mapped[PlanStatus] = mapped_column(
        Enum(PlanStatus, name="plan_status"), 
        default=PlanStatus.NOT_STARTED,
        nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Additional fields for better planning tracking
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    goals: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Relationships
    tasks: Mapped[List["Task"]] = relationship("Task", back_populates="planning_session", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<PlanningSession(id={self.id}, user_id='{self.user_id}', date={self.date}, status={self.status.value})>"
    
    @property
    def is_active(self) -> bool:
        """Check if the planning session is currently active."""
        return self.status == PlanStatus.IN_PROGRESS
    
    @property
    def is_completed(self) -> bool:
        """Check if the planning session is completed."""
        return self.status == PlanStatus.COMPLETE
    
    def mark_complete(self) -> None:
        """Mark the planning session as complete."""
        self.status = PlanStatus.COMPLETE
        self.completed_at = datetime.utcnow()
    
    def mark_in_progress(self) -> None:
        """Mark the planning session as in progress."""
        self.status = PlanStatus.IN_PROGRESS


class TaskStatus(PyEnum):
    """Status of a task."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskPriority(PyEnum):
    """Priority levels for tasks."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class Task(Base):
    """Individual tasks within a planning session."""
    
    __tablename__ = "tasks"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    planning_session_id: Mapped[int] = mapped_column(Integer, ForeignKey("planning_sessions.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"),
        default=TaskStatus.PENDING,
        nullable=False
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, name="task_priority"),
        default=TaskPriority.MEDIUM,
        nullable=False
    )
    
    # Time tracking
    estimated_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scheduled_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scheduled_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # External integrations
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    slack_thread_ts: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Relationships
    planning_session: Mapped["PlanningSession"] = relationship("PlanningSession", back_populates="tasks")
    reminders: Mapped[List["Reminder"]] = relationship("Reminder", back_populates="task", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Task(id={self.id}, title='{self.title}', status={self.status.value}, priority={self.priority.value})>"
    
    @property
    def is_completed(self) -> bool:
        """Check if the task is completed."""
        return self.status == TaskStatus.COMPLETED
    
    @property
    def is_overdue(self) -> bool:
        """Check if the task is overdue."""
        if not self.scheduled_end:
            return False
        return datetime.utcnow() > self.scheduled_end and not self.is_completed
    
    def mark_started(self) -> None:
        """Mark the task as started."""
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.utcnow()
    
    def mark_completed(self) -> None:
        """Mark the task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        
        # Calculate actual duration if we have a start time
        if self.started_at:
            duration = datetime.utcnow() - self.started_at
            self.actual_duration_minutes = int(duration.total_seconds() / 60)


class ReminderType(PyEnum):
    """Types of reminders."""
    TASK_START = "TASK_START"
    TASK_DUE = "TASK_DUE"
    PLANNING_SESSION = "PLANNING_SESSION"
    CUSTOM = "CUSTOM"


class Reminder(Base):
    """Reminders for tasks and planning sessions."""
    
    __tablename__ = "reminders"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("tasks.id"), nullable=True)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    reminder_type: Mapped[ReminderType] = mapped_column(
        Enum(ReminderType, name="reminder_type"),
        nullable=False
    )
    
    # Reminder content
    message: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Status tracking
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    
    # External tracking
    scheduler_job_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    slack_message_ts: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Relationships
    task: Mapped[Optional["Task"]] = relationship("Task", back_populates="reminders")
    
    def __repr__(self):
        return f"<Reminder(id={self.id}, type={self.reminder_type.value}, scheduled_at={self.scheduled_at}, is_sent={self.is_sent})>"
    
    @property
    def is_overdue(self) -> bool:
        """Check if the reminder is overdue (should have been sent)."""
        return datetime.utcnow() > self.scheduled_at and not self.is_sent
    
    def mark_sent(self) -> None:
        """Mark the reminder as sent."""
        self.is_sent = True
        self.sent_at = datetime.utcnow()
    
    def cancel(self) -> None:
        """Cancel the reminder."""
        self.is_cancelled = True


class UserPreferences(Base):
    """User preferences and settings."""
    
    __tablename__ = "user_preferences"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    
    # Reminder preferences
    default_reminder_minutes_before: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    enable_task_start_reminders: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_task_due_reminders: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_planning_reminders: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Planning preferences
    preferred_planning_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # HH:MM format
    auto_schedule_tasks: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_task_duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    
    # Timezone and locale
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<UserPreferences(user_id='{self.user_id}', timezone='{self.timezone}')>"
