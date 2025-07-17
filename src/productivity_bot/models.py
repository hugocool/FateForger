"""
Database models for the productivity bot.
"""

from datetime import date as DateType
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)

# Import logger
from .common import get_logger
logger = get_logger(__name__)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .common import Base


class EventStatus(PyEnum):
    """Status of calendar events."""

    UPCOMING = "UPCOMING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class BaseEvent(Base):
    """
    Base model for all calendar events - foundation for event-driven agents.
    Captures common fields for any calendar event across different sources.
    """

    __tablename__ = "base_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    # Core event details
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Timing
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Status and metadata
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status"),
        default=EventStatus.UPCOMING,
        nullable=False,
    )

    # Extended metadata as JSON for flexibility
    event_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )

    # Source tracking
    source: Mapped[str] = mapped_column(
        String(50), default="google_calendar", nullable=False
    )
    external_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )  # Original source ID

    # Sync tracking
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Bot metadata for agent routing
    bot_managed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    agent_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # planner, haunter, etc.

    # Relationships
    planning_sessions: Mapped[List["PlanningSession"]] = relationship(
        "PlanningSession", back_populates="base_event", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<BaseEvent(event_id='{self.event_id}', title='{self.title}', start_time={self.start_time}, status={self.status.value})>"

    @property
    def is_upcoming(self) -> bool:
        """Check if the event is upcoming."""
        return (
            self.status == EventStatus.UPCOMING and self.start_time > datetime.utcnow()
        )

    @property
    def is_past(self) -> bool:
        """Check if the event is in the past."""
        return self.end_time < datetime.utcnow()

    @property
    def duration_minutes(self) -> int:
        """Get event duration in minutes."""
        duration = self.end_time - self.start_time
        return int(duration.total_seconds() / 60)

    def mark_completed(self) -> None:
        """Mark the event as completed."""
        self.status = EventStatus.COMPLETED

    def mark_cancelled(self) -> None:
        """Mark the event as cancelled."""
        self.status = EventStatus.CANCELLED


class PlanStatus(PyEnum):
    """Status of a planning session."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class PlanningSession(Base):
    """
    Tracks the state of planning sessions for users.
    Wraps a daily planning event with session-specific metadata.
    """

    __tablename__ = "planning_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    date: Mapped[DateType] = mapped_column(Date, index=True, nullable=False)

    # Reference to the BaseEvent
    event_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("base_events.event_id"), nullable=True
    )

    status: Mapped[PlanStatus] = mapped_column(
        Enum(PlanStatus, name="plan_status"),
        default=PlanStatus.NOT_STARTED,
        nullable=False,
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Slack thread tracking
    thread_ts: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    channel_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Nudge tracking for haunting
    next_nudge_attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_nudge_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Scheduler tracking
    scheduler_job_id: Mapped[Optional[str]] = mapped_column(
        String(100), index=True, nullable=True
    )

    # Slack scheduled message tracking
    slack_scheduled_message_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    # Haunt attempt tracking for escalation
    haunt_attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Additional fields for better planning tracking
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    goals: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    base_event: Mapped[Optional["BaseEvent"]] = relationship(
        "BaseEvent", back_populates="planning_sessions"
    )
    tasks: Mapped[List["Task"]] = relationship(
        "Task", back_populates="planning_session", cascade="all, delete-orphan"
    )

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

    async def recreate_event(self) -> bool:
        """
        Recreate the calendar event for this planning session.

        This method attempts to recreate the associated calendar event
        using the MCP calendar integration.

        Returns:
            True if successful, False otherwise
        """
        try:
            # Import here to avoid circular imports
            from datetime import timedelta
            from .mcp_integration import get_mcp_client

            # Get MCP client
            mcp_client = await get_mcp_client()
            if not mcp_client:
                logger.warning("MCP client not available for event recreation")
                return False

            # Create event using MCP client
            event_data = {
                "summary": f"Daily Planning Session - {self.date}",
                "description": f"Planning session for {self.user_id}\n\nGoals: {self.goals or 'Not set'}",
                "start": {
                    "dateTime": self.scheduled_for.isoformat(),
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": (self.scheduled_for + timedelta(hours=1)).isoformat(),
                    "timeZone": "UTC",
                },
            }

            # Create event via MCP
            created_event = await mcp_client.create_event(
                title=event_data["summary"],
                start_time=event_data["start"]["dateTime"], 
                end_time=event_data["end"]["dateTime"],
                description=event_data["description"]
            )

            if created_event and created_event.get("id"):
                # Update the event_id if successful
                self.event_id = created_event.get("id")
                logger.info(f"Successfully recreated event for planning session {self.id}")
                return True
            else:
                logger.warning(f"Failed to create event for planning session {self.id}")
                return False

        except Exception as e:
            logger.error(f"Error recreating event for planning session {self.id}: {e}")
            return False


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
    planning_session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("planning_sessions.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"), default=TaskStatus.PENDING, nullable=False
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, name="task_priority"),
        default=TaskPriority.MEDIUM,
        nullable=False,
    )

    # Time tracking
    estimated_duration_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    actual_duration_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    scheduled_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scheduled_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # External integrations
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    slack_thread_ts: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    planning_session: Mapped["PlanningSession"] = relationship(
        "PlanningSession", back_populates="tasks"
    )
    reminders: Mapped[List["Reminder"]] = relationship(
        "Reminder", back_populates="task", cascade="all, delete-orphan"
    )

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
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    reminder_type: Mapped[ReminderType] = mapped_column(
        Enum(ReminderType, name="reminder_type"), nullable=False
    )

    # Reminder content
    message: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Status tracking
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

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
    default_reminder_minutes_before: Mapped[int] = mapped_column(
        Integer, default=15, nullable=False
    )
    enable_task_start_reminders: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    enable_task_due_reminders: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    enable_planning_reminders: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # Planning preferences
    preferred_planning_time: Mapped[Optional[str]] = mapped_column(
        String(5), nullable=True
    )  # HH:MM format
    auto_schedule_tasks: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    default_task_duration_minutes: Mapped[int] = mapped_column(
        Integer, default=30, nullable=False
    )

    # Timezone and locale
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return (
            f"<UserPreferences(user_id='{self.user_id}', timezone='{self.timezone}')>"
        )


class CalendarEvent(Base):
    """Calendar events synchronized from Google Calendar via MCP."""

    __tablename__ = "calendar_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    # Event details
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Timing
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Status and metadata
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status"),
        default=EventStatus.UPCOMING,
        nullable=False,
    )

    # Google Calendar specific
    google_status: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # confirmed, tentative, cancelled
    google_updated: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    google_etag: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Attendees and organizer
    organizer_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    attendees_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Reminders and notifications
    has_reminders: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reminder_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Sync tracking
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Job scheduling
    scheduler_job_id: Mapped[Optional[str]] = mapped_column(
        String(100), index=True, nullable=True
    )

    # Relationships
    reminder_jobs: Mapped[List["CalendarReminderJob"]] = relationship(
        "CalendarReminderJob",
        back_populates="event",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<CalendarEvent(event_id='{self.event_id}', title='{self.title}', start_time={self.start_time}, status={self.status.value})>"

    @property
    def is_upcoming(self) -> bool:
        """Check if the event is upcoming."""
        return (
            self.status == EventStatus.UPCOMING and self.start_time > datetime.utcnow()
        )

    @property
    def is_past(self) -> bool:
        """Check if the event is in the past."""
        return self.end_time < datetime.utcnow()

    @property
    def duration_minutes(self) -> int:
        """Get event duration in minutes."""
        duration = self.end_time - self.start_time
        return int(duration.total_seconds() / 60)

    def mark_completed(self) -> None:
        """Mark the event as completed."""
        self.status = EventStatus.COMPLETED

    def mark_cancelled(self) -> None:
        """Mark the event as cancelled."""
        self.status = EventStatus.CANCELLED


class CalendarReminderJob(Base):
    """Tracks individual reminder jobs for calendar events."""

    __tablename__ = "calendar_reminder_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("calendar_events.event_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Relationship back to event
    event: Mapped["CalendarEvent"] = relationship(
        "CalendarEvent", back_populates="reminder_jobs"
    )

    def __repr__(self):
        return f"<CalendarReminderJob(id={self.id}, event_id='{self.event_id}', job_id='{self.job_id}')>"


class CalendarSync(Base):
    """Tracks synchronization state for calendar resources."""

    __tablename__ = "calendar_sync"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Sync tokens
    sync_token: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    next_sync_token: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    # Watch channel info
    channel_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    channel_expiration: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )

    # Sync status
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_successful_sync_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<CalendarSync(calendar_id='{self.calendar_id}', last_sync_at={self.last_sync_at})>"

    @property
    def needs_sync(self) -> bool:
        """Check if calendar needs synchronization."""
        if not self.last_sync_at:
            return True

        # Sync at least every hour
        from datetime import timedelta

        return datetime.utcnow() - self.last_sync_at > timedelta(hours=1)

    @property
    def watch_expired(self) -> bool:
        """Check if the watch channel has expired."""
        if not self.channel_expiration:
            return True
        return datetime.utcnow() > self.channel_expiration
