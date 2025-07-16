"""
Database operations for planning sessions and related models.
"""

from datetime import datetime, date, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, and_, or_, desc
from sqlalchemy.orm import selectinload

from .models import (
    PlanningSession, 
    PlanStatus,
    Reminder,
    ReminderType,
    UserPreferences
)
from .common import get_logger, get_config

logger = get_logger("database")

# Database setup
def get_database_engine():
    """Get the database engine."""
    config = get_config()
    database_url = getattr(config, 'database_url', 'sqlite+aiosqlite:///data/planner.db')
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
        goals: Optional[str] = None
    ) -> PlanningSession:
        """Create a new planning session."""
        async with get_db_session() as db:
            session = PlanningSession(
                user_id=user_id,
                date=session_date,
                scheduled_for=scheduled_for,
                goals=goals,
                status=PlanStatus.NOT_STARTED
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            logger.info(f"Created planning session {session.id} for user {user_id}")
            return session
    
    @staticmethod
    async def get_session_by_id(session_id: int) -> Optional[PlanningSession]:
        """Get a planning session by ID."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .options(selectinload(PlanningSession.tasks))
                .where(PlanningSession.id == session_id)
            )
            return result.scalar_one_or_none()
    
    @staticmethod
    async def get_user_session_for_date(user_id: str, session_date: date) -> Optional[PlanningSession]:
        """Get a user's planning session for a specific date."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .options(selectinload(PlanningSession.tasks))
                .where(
                    and_(
                        PlanningSession.user_id == user_id,
                        PlanningSession.date == session_date
                    )
                )
            )
            return result.scalar_one_or_none()
    
    @staticmethod
    async def get_user_active_session(user_id: str) -> Optional[PlanningSession]:
        """Get a user's currently active planning session."""
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession)
                .options(selectinload(PlanningSession.tasks))
                .where(
                    and_(
                        PlanningSession.user_id == user_id,
                        PlanningSession.status == PlanStatus.IN_PROGRESS
                    )
                )
                .order_by(desc(PlanningSession.created_at))
            )
            return result.scalar_one_or_none()
    
    @staticmethod
    async def get_user_sessions(
        user_id: str, 
        limit: int = 10, 
        status: Optional[PlanStatus] = None
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


class TaskService:
    """Service for managing tasks."""
    
    @staticmethod
    async def create_task(
        planning_session_id: int,
        title: str,
        description: Optional[str] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
        estimated_duration_minutes: Optional[int] = None,
        scheduled_start: Optional[datetime] = None,
        scheduled_end: Optional[datetime] = None
    ) -> Task:
        """Create a new task."""
        async with get_db_session() as db:
            task = Task(
                planning_session_id=planning_session_id,
                title=title,
                description=description,
                priority=priority,
                estimated_duration_minutes=estimated_duration_minutes,
                scheduled_start=scheduled_start,
                scheduled_end=scheduled_end
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            logger.info(f"Created task {task.id}: {title}")
            return task
    
    @staticmethod
    async def get_task_by_id(task_id: int) -> Optional[Task]:
        """Get a task by ID."""
        async with get_db_session() as db:
            result = await db.execute(
                select(Task)
                .options(selectinload(Task.reminders))
                .where(Task.id == task_id)
            )
            return result.scalar_one_or_none()
    
    @staticmethod
    async def get_session_tasks(session_id: int) -> List[Task]:
        """Get all tasks for a planning session."""
        async with get_db_session() as db:
            result = await db.execute(
                select(Task)
                .where(Task.planning_session_id == session_id)
                .order_by(Task.priority.desc(), Task.created_at)
            )
            return result.scalars().all()
    
    @staticmethod
    async def get_user_tasks_due_soon(user_id: str, hours_ahead: int = 24) -> List[Task]:
        """Get tasks due in the next N hours for a user."""
        async with get_db_session() as db:
            cutoff_time = datetime.utcnow() + timedelta(hours=hours_ahead)
            
            result = await db.execute(
                select(Task)
                .join(PlanningSession)
                .where(
                    and_(
                        PlanningSession.user_id == user_id,
                        Task.scheduled_end <= cutoff_time,
                        Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
                    )
                )
                .order_by(Task.scheduled_end)
            )
            return result.scalars().all()
    
    @staticmethod
    async def update_task_status(task_id: int, status: TaskStatus) -> bool:
        """Update a task's status."""
        async with get_db_session() as db:
            result = await db.execute(
                select(Task).where(Task.id == task_id)
            )
            task = result.scalar_one_or_none()
            
            if not task:
                return False
            
            old_status = task.status
            task.status = status
            
            if status == TaskStatus.IN_PROGRESS and old_status != TaskStatus.IN_PROGRESS:
                task.started_at = datetime.utcnow()
            elif status == TaskStatus.COMPLETED:
                task.completed_at = datetime.utcnow()
                # Calculate actual duration if started
                if task.started_at:
                    duration = datetime.utcnow() - task.started_at
                    task.actual_duration_minutes = int(duration.total_seconds() / 60)
            
            await db.commit()
            logger.info(f"Updated task {task_id} status from {old_status.value} to {status.value}")
            return True
    
    @staticmethod
    async def get_overdue_tasks() -> List[Task]:
        """Get all overdue tasks."""
        async with get_db_session() as db:
            now = datetime.utcnow()
            
            result = await db.execute(
                select(Task)
                .where(
                    and_(
                        Task.scheduled_end < now,
                        Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS])
                    )
                )
                .order_by(Task.scheduled_end)
            )
            return result.scalars().all()


class ReminderService:
    """Service for managing reminders."""
    
    @staticmethod
    async def create_reminder(
        user_id: str,
        reminder_type: ReminderType,
        message: str,
        scheduled_at: datetime,
        task_id: Optional[int] = None,
        scheduler_job_id: Optional[str] = None
    ) -> Reminder:
        """Create a new reminder."""
        async with get_db_session() as db:
            reminder = Reminder(
                user_id=user_id,
                task_id=task_id,
                reminder_type=reminder_type,
                message=message,
                scheduled_at=scheduled_at,
                scheduler_job_id=scheduler_job_id
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
                        Reminder.is_cancelled == False
                    )
                )
                .order_by(Reminder.scheduled_at)
            )
            return result.scalars().all()
    
    @staticmethod
    async def mark_reminder_sent(reminder_id: int, slack_message_ts: Optional[str] = None) -> bool:
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
