from __future__ import annotations

"""
Database operations for planning sessions and related models.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from . import models  # Import the models module directly
from .common import get_config, get_logger

logger = get_logger("database")


# Database setup
def get_database_engine():
    """Get the database engine."""
    config = get_config()
    database_url = getattr(
        config, "database_url", "sqlite+aiosqlite:///data/admonish.db"
    )
    return create_async_engine(database_url, echo=False)


def get_session_factory():
    """Get database session factory."""
    engine = get_database_engine()
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


SessionLocal = get_session_factory()


@asynccontextmanager
async def get_db_session():
    """Get a database session (async context manager)."""
    session = SessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


class PlanningSessionService:
    """Service for managing planning sessions."""

    @staticmethod
    async def create_session(
        user_id: str,
        session_date: date,
        scheduled_for: datetime,
    ):
        """Create a new planning session with minimal metadata."""
        async with get_db_session() as db:
            session = models.PlanningSession(
                user_id=user_id,
                date=session_date,
                scheduled_for=scheduled_for,
                status=models.PlanStatus.NOT_STARTED,
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            logger.info(f"Created planning session {session.id} for user {user_id}")
            return session

    @staticmethod
    async def get_session_by_id(session_id: int):
        """Get a planning session by ID."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .options(selectinload(PlanningSession.tasks))
                .where(PlanningSession.id == session_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_user_session_for_date(user_id: str, session_date: date):
        """Get a user's planning session for a specific date."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .options(selectinload(PlanningSession.tasks))
                .where(
                    and_(
                        PlanningSession.user_id == user_id,
                        PlanningSession.date == session_date,
                    )
                )
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_user_active_session(user_id: str) -> Optional[models.PlanningSession]:
        """Get a user's currently active planning session."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .options(selectinload(PlanningSession.tasks))
                .where(
                    and_(
                        PlanningSession.user_id == user_id,
                        PlanningSession.status == PlanStatus.IN_PROGRESS,
                    )
                )
                .order_by(desc(PlanningSession.created_at))
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_session_by_thread(
        thread_ts: str, channel_id: str
    ) -> Optional[PlanningSession]:
        """Get a planning session by Slack thread timestamp and channel."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .options(selectinload(PlanningSession.tasks))
                .where(
                    and_(
                        PlanningSession.thread_ts == thread_ts,
                        PlanningSession.channel_id == channel_id,
                    )
                )
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_user_sessions(
        user_id: str, limit: int = 10, status: Optional[PlanStatus] = None
    ) -> List[PlanningSession]:
        """Get a user's planning sessions."""
        async with get_db_session() as db:
            query = (
                select(PlanningSession)
                .where(PlanningSession.user_id == user_id)
                .order_by(desc(PlanningSession.date))
                .limit(limit)
            )

            if status:
                query = query.where(PlanningSession.status == status)

            result = await db.execute(query)
            return result.scalars().all()

    @staticmethod
    async def update_session_status(session_id: int, status: PlanStatus) -> bool:
        """Update a planning session's status."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == session_id)
            )
            session = result.scalar_one_or_none()

            if not session:
                return False

            session.status = status
            if status == PlanStatus.COMPLETE:
                session.completed_at = datetime.utcnow()

            await db.commit()
            logger.info(f"Updated session {session_id} status to {status.value}")
            return True

    @staticmethod
    async def add_session_notes(session_id: int, notes: str) -> bool:
        """Add notes to a planning session."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == session_id)
            )
            session = result.scalar_one_or_none()

            if not session:
                return False

            session.notes = notes
            await db.commit()
            return True

    @staticmethod
    async def update_session_job_id(session_id: int, job_id: str) -> bool:
        """Store the APScheduler job ID on the session for later cancellation."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == session_id)
            )
            session = result.scalar_one_or_none()

            if not session:
                return False

            session.scheduler_job_id = job_id
            await db.commit()
            logger.info(f"Updated session {session_id} with job ID {job_id}")
            return True

    @staticmethod
    async def update_session(session: PlanningSession) -> bool:
        """Update a planning session with changes from the provided object."""
        async with get_db_session() as db:
            await db.merge(session)
            await db.commit()
            logger.info(f"Updated session {session.id}")
            return True

    @staticmethod
    async def get_recent_sessions_for_channel(channel_id: str) -> List[PlanningSession]:
        """
        Get recent planning sessions for users in a channel.

        This is a simplified implementation - in practice you'd store
        channel associations or thread references.

        Args:
            channel_id: Slack channel ID

        Returns:
            List of recent planning sessions
        """
        async with get_db_session() as db:
            # For now, return recent sessions from the last 24 hours
            # In practice, you'd have a proper channel->user mapping
            from datetime import datetime, timedelta

            cutoff_time = datetime.utcnow() - timedelta(hours=24)

            result = await db.execute(
                select(PlanningSession)
                .where(PlanningSession.created_at >= cutoff_time)
                .order_by(desc(PlanningSession.created_at))
                .limit(10)
            )
            return result.scalars().all()

    @staticmethod
    async def postpone_session(session_id: int, minutes: int) -> datetime:
        """
        Postpone a planning session by the specified number of minutes.

        Args:
            session_id: Planning session ID
            minutes: Number of minutes to postpone

        Returns:
            New scheduled time
        """
        async with get_db_session() as db:
            from datetime import timedelta

            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == session_id)
            )
            session = result.scalar_one_or_none()

            if not session:
                raise ValueError(f"Session {session_id} not found")

            # Add the postponement time
            new_time = session.scheduled_for + timedelta(minutes=minutes)
            session.scheduled_for = new_time

            await db.commit()
            logger.info(
                f"Postponed session {session_id} by {minutes} minutes to {new_time}"
            )

            return new_time

    @staticmethod
    async def update_session_thread_info(
        session_id: int, thread_ts: str, channel_id: str
    ) -> bool:
        """
        Update a planning session with thread timestamp and channel ID.

        Args:
            session_id: Planning session ID
            thread_ts: Slack thread timestamp
            channel_id: Slack channel ID

        Returns:
            True if updated successfully, False otherwise
        """
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == session_id)
            )
            session = result.scalar_one_or_none()

            if not session:
                logger.warning(f"Session {session_id} not found for thread update")
                return False

            # Update thread information
            session.thread_ts = thread_ts
            session.channel_id = channel_id

            await db.commit()
            logger.info(
                f"Updated session {session_id} with thread_ts={thread_ts}, channel_id={channel_id}"
            )

            return True

    @staticmethod
    async def complete_session(session_id: int) -> bool:
        """
        Mark a planning session as complete.

        Args:
            session_id: Planning session ID

        Returns:
            True if successful, False otherwise
        """
        return await PlanningSessionService.update_session_status(
            session_id, PlanStatus.COMPLETE
        )


class ReminderService:
    """Service for managing reminders."""

    @staticmethod
    async def create_reminder(
        user_id: str,
        reminder_type: ReminderType,
        message: str,
        scheduled_at: datetime,
        task_id: Optional[int] = None,
        scheduler_job_id: Optional[str] = None,
    ) -> Reminder:
        """Create a new reminder."""
        async with get_db_session() as db:
            reminder = Reminder(
                user_id=user_id,
                task_id=task_id,
                reminder_type=reminder_type,
                message=message,
                scheduled_at=scheduled_at,
                scheduler_job_id=scheduler_job_id,
            )
            db.add(reminder)
            await db.commit()
            await db.refresh(reminder)
            logger.info(f"Created reminder {reminder.id} for user {user_id}")
            return reminder

    @staticmethod
    async def get_pending_reminders() -> List[Reminder]:
        """Get all pending reminders that should be sent."""
        async with get_db_session() as db:
            now = datetime.utcnow()

            result = await db.execute(
                select(Reminder)
                .where(
                    and_(
                        Reminder.scheduled_at <= now,
                        Reminder.is_sent == False,
                        Reminder.is_cancelled == False,
                    )
                )
                .order_by(Reminder.scheduled_at)
            )
            return result.scalars().all()

    @staticmethod
    async def mark_reminder_sent(
        reminder_id: int, slack_message_ts: Optional[str] = None
    ) -> bool:
        """Mark a reminder as sent."""
        async with get_db_session() as db:
            result = await db.execute(
                select(Reminder).where(Reminder.id == reminder_id)
            )
            reminder = result.scalar_one_or_none()

            if not reminder:
                return False

            reminder.mark_sent()
            if slack_message_ts:
                reminder.slack_message_ts = slack_message_ts

            await db.commit()
            logger.info(f"Marked reminder {reminder_id} as sent")
            return True


class UserPreferencesService:
    """Service for managing user preferences."""

    @staticmethod
    async def get_or_create_preferences(user_id: str) -> UserPreferences:
        """Get user preferences or create default ones."""
        async with get_db_session() as db:
            result = await db.execute(
                select(UserPreferences).where(UserPreferences.user_id == user_id)
            )
            preferences = result.scalar_one_or_none()

            if not preferences:
                preferences = UserPreferences(user_id=user_id)
                db.add(preferences)
                await db.commit()
                await db.refresh(preferences)
                logger.info(f"Created default preferences for user {user_id}")

            return preferences

    @staticmethod
    async def update_preferences(user_id: str, **kwargs) -> bool:
        """Update user preferences."""
        async with get_db_session() as db:
            result = await db.execute(
                select(UserPreferences).where(UserPreferences.user_id == user_id)
            )
            preferences = result.scalar_one_or_none()

            if not preferences:
                preferences = UserPreferences(user_id=user_id)
                db.add(preferences)

            for key, value in kwargs.items():
                if hasattr(preferences, key):
                    setattr(preferences, key, value)

            await db.commit()
            logger.info(f"Updated preferences for user {user_id}")
            return True


class CalendarEventService:
    """Service class for calendar event operations."""

    @staticmethod
    async def get_event_by_id(event_id: str) -> Optional[CalendarEvent]:
        """Get a calendar event by ID."""
        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarEvent).where(CalendarEvent.event_id == event_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_upcoming_events(days_ahead: int = 7) -> List[CalendarEvent]:
        """Get upcoming calendar events."""
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(days=days_ahead)

        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarEvent)
                .where(
                    and_(
                        CalendarEvent.start_time >= start_time,
                        CalendarEvent.start_time <= end_time,
                        CalendarEvent.status == EventStatus.UPCOMING,
                    )
                )
                .order_by(CalendarEvent.start_time)
            )
            return result.scalars().all()

    @staticmethod
    async def get_events_by_calendar(
        calendar_id: str, days_ahead: int = 30
    ) -> List[CalendarEvent]:
        """Get events for a specific calendar."""
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(days=days_ahead)

        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarEvent)
                .where(
                    and_(
                        CalendarEvent.calendar_id == calendar_id,
                        CalendarEvent.start_time >= start_time,
                        CalendarEvent.start_time <= end_time,
                    )
                )
                .order_by(CalendarEvent.start_time)
            )
            return result.scalars().all()

    @staticmethod
    async def upsert_event(event_data: dict) -> CalendarEvent:
        """Upsert a calendar event."""
        async with get_db_session() as db:
            event_id = event_data["event_id"]

            result = await db.execute(
                select(CalendarEvent).where(CalendarEvent.event_id == event_id)
            )
            event = result.scalar_one_or_none()

            if event:
                # Update existing event
                for key, value in event_data.items():
                    if hasattr(event, key):
                        setattr(event, key, value)
                event.last_synced_at = datetime.utcnow()
            else:
                # Create new event
                event = CalendarEvent(**event_data)
                event.last_synced_at = datetime.utcnow()
                db.add(event)

            await db.commit()
            await db.refresh(event)
            return event

    @staticmethod
    async def mark_event_completed(event_id: str) -> bool:
        """Mark an event as completed."""
        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarEvent).where(CalendarEvent.event_id == event_id)
            )
            event = result.scalar_one_or_none()

            if event:
                event.status = EventStatus.COMPLETED
                await db.commit()
                logger.info(f"Marked event {event_id} as completed")
                return True
            return False

    @staticmethod
    async def mark_event_cancelled(event_id: str) -> bool:
        """Mark an event as cancelled."""
        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarEvent).where(CalendarEvent.event_id == event_id)
            )
            event = result.scalar_one_or_none()

            if event:
                event.status = EventStatus.CANCELLED
                await db.commit()
                logger.info(f"Marked event {event_id} as cancelled")
                return True
            return False


class CalendarSyncService:
    """Service class for calendar synchronization operations."""

    @staticmethod
    async def get_sync_state(calendar_id: str) -> Optional[CalendarSync]:
        """Get synchronization state for a calendar."""
        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarSync).where(CalendarSync.calendar_id == calendar_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def update_sync_state(calendar_id: str, **kwargs) -> CalendarSync:
        """Update synchronization state."""
        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarSync).where(CalendarSync.calendar_id == calendar_id)
            )
            sync_state = result.scalar_one_or_none()

            if not sync_state:
                sync_state = CalendarSync(
                    calendar_id=calendar_id, resource_id=calendar_id
                )
                db.add(sync_state)

            for key, value in kwargs.items():
                if hasattr(sync_state, key):
                    setattr(sync_state, key, value)

            sync_state.last_sync_at = datetime.utcnow()

            await db.commit()
            await db.refresh(sync_state)
            return sync_state

    @staticmethod
    async def mark_sync_error(calendar_id: str, error: str) -> None:
        """Mark a synchronization error."""
        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarSync).where(CalendarSync.calendar_id == calendar_id)
            )
            sync_state = result.scalar_one_or_none()

            if sync_state:
                sync_state.sync_error = error
                sync_state.last_sync_at = datetime.utcnow()
                await db.commit()

    @staticmethod
    async def get_calendars_needing_sync() -> List[CalendarSync]:
        """Get calendars that need synchronization."""
        cutoff_time = datetime.utcnow() - timedelta(hours=1)

        async with get_db_session() as db:
            result = await db.execute(
                select(CalendarSync).where(
                    or_(
                        CalendarSync.last_sync_at.is_(None),
                        CalendarSync.last_sync_at < cutoff_time,
                    )
                )
            )
            return result.scalars().all()
