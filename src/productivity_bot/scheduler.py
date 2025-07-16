"""
Dedicated scheduler module with singleton APScheduler and SQLAlchemy job store.

This module provides a centralized scheduling system for the productivity bot,
handling calendar event reminders, planning session notifications, and background
tasks with persistence and reliability features.

Key Features:
    - Singleton APScheduler instance with SQLAlchemy persistence
    - Automatic job recovery on restart
    - Event-driven logging and monitoring
    - Graceful error handling with misfire protection
    - Support for async job execution

Example:
    ```python
    from productivity_bot.scheduler import schedule_event_haunt

    # Schedule a reminder 15 minutes before an event
    job_id = schedule_event_haunt(
        event_id="cal_event_123",
        when=event_start - timedelta(minutes=15)
    )
    ```
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobExecutionEvent
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.job import Job
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .common import get_config, get_logger
from .database import get_database_engine

logger = get_logger("scheduler")

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """
    Get the global scheduler instance.

    Returns:
        The singleton APScheduler instance, creating it if it doesn't exist.
    """
    global scheduler
    if scheduler is None:
        scheduler = create_scheduler()
    return scheduler


def job_executed_listener(event: JobExecutionEvent) -> None:
    """
    Handle successful job execution events.

    Args:
        event: The job execution event from APScheduler.
    """
    logger.info(f"Job {event.job_id} executed successfully")


def job_error_listener(event: JobExecutionEvent) -> None:
    """
    Handle job execution error events.

    Args:
        event: The job execution event containing error information.
    """
    logger.error(f"Job {event.job_id} failed: {event.exception}")


def create_scheduler() -> AsyncIOScheduler:
    """
    Create and configure the APScheduler instance with SQLAlchemy job store.

    Configures a persistent scheduler that can survive application restarts
    by storing job data in the same database as the application.

    Returns:
        Configured AsyncIOScheduler instance ready for use.

    Raises:
        Exception: If scheduler configuration fails.
    """
    config = get_config()

    # Get the sync engine for SQLAlchemy job store
    engine = get_database_engine()

    # Configure job store to persist jobs in database
    # APScheduler will create the apscheduler_jobs table automatically
    jobstore = SQLAlchemyJobStore(engine=engine.sync_engine)

    # Configure executor for async jobs
    executor = AsyncIOExecutor()

    # Job defaults
    job_defaults = {"coalesce": False, "max_instances": 3, "misfire_grace_time": 30}

    # Create scheduler
    scheduler_instance = AsyncIOScheduler(
        jobstores={"default": jobstore},
        executors={"default": executor},
        job_defaults=job_defaults,
        timezone=config.scheduler_timezone,
    )

    # Add event listeners
    scheduler_instance.add_listener(job_executed_listener, EVENT_JOB_EXECUTED)
    scheduler_instance.add_listener(job_error_listener, EVENT_JOB_ERROR)

    logger.info("APScheduler configured with SQLAlchemy job store")
    return scheduler_instance


async def start_scheduler() -> None:
    """
    Start the scheduler instance.

    Starts the APScheduler if it's not already running. Safe to call multiple times.

    Raises:
        Exception: If scheduler fails to start.
    """
    scheduler_instance = get_scheduler()

    if not scheduler_instance.running:
        scheduler_instance.start()
        logger.info("APScheduler started")
    else:
        logger.warning("APScheduler is already running")


async def stop_scheduler() -> None:
    """
    Stop the scheduler gracefully.

    Waits for running jobs to complete before shutting down.
    Safe to call even if scheduler is not running.
    """
    scheduler_instance = get_scheduler()

    if scheduler_instance.running:
        scheduler_instance.shutdown(wait=True)
        logger.info("APScheduler stopped")
    else:
        logger.warning("APScheduler is not running")


def schedule_event_haunt(
    event_id: str, when: datetime, reminder_minutes: int = 15
) -> str:
    """
    Schedule a haunt job for a calendar event.

    Creates a scheduled job that will trigger the haunter bot to send
    a reminder about an upcoming calendar event.

    Args:
        event_id: The calendar event ID to create reminder for.
        when: When to send the reminder (absolute datetime).
        reminder_minutes: How many minutes before event start to remind.
            Used for logging and context, actual timing based on 'when' parameter.

    Returns:
        The job ID for the scheduled job, format: "haunt_event_{event_id}".

    Raises:
        Exception: If job scheduling fails.

    Example:
        >>> from datetime import datetime, timedelta
        >>> event_start = datetime(2024, 1, 15, 14, 0)  # 2 PM
        >>> reminder_time = event_start - timedelta(minutes=15)
        >>> job_id = schedule_event_haunt("cal_123", reminder_time, 15)
        >>> print(job_id)  # "haunt_event_cal_123"
    """
    scheduler_instance = get_scheduler()

    job_id = f"haunt_event_{event_id}"

    # Calculate the actual reminder time
    reminder_time = when

    try:
        # Remove existing job if it exists
        try:
            scheduler_instance.remove_job(job_id)
            logger.debug(f"Removed existing job {job_id}")
        except Exception:
            pass  # Job doesn't exist, that's fine

        # Schedule new job
        scheduler_instance.add_job(
            func="productivity_bot.haunter_bot:haunt_event",
            trigger="date",
            run_date=reminder_time,
            args=[event_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,  # 5 minutes grace period
        )

        logger.info(f"Scheduled haunt job {job_id} for {reminder_time}")
        return job_id

    except Exception as e:
        logger.error(f"Failed to schedule haunt job for event {event_id}: {e}")
        raise


def schedule_planning_session_haunt(session_id: int, when: datetime) -> str:
    """
    Schedule a haunt job for a planning session.

    Creates a scheduled job that will trigger the haunter bot to send
    a reminder about a planning session that needs attention.

    Args:
        session_id: The planning session ID to create reminder for.
        when: When to send the reminder (absolute datetime).

    Returns:
        The job ID for the scheduled job, format: "haunt_session_{session_id}".

    Raises:
        Exception: If job scheduling fails.

    Example:
        >>> from datetime import datetime
        >>> session_time = datetime(2024, 1, 15, 9, 0)  # 9 AM planning time
        >>> job_id = schedule_planning_session_haunt(42, session_time)
        >>> print(job_id)  # "haunt_session_42"
    """
    scheduler_instance = get_scheduler()

    job_id = f"haunt_session_{session_id}"

    try:
        # Remove existing job if it exists
        try:
            scheduler_instance.remove_job(job_id)
            logger.debug(f"Removed existing job {job_id}")
        except Exception:
            pass  # Job doesn't exist, that's fine

        # Schedule new job
        scheduler_instance.add_job(
            func="productivity_bot.haunter_bot:haunt_planning_session",
            trigger="date",
            run_date=when,
            args=[session_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,  # 5 minutes grace period
        )

        logger.info(f"Scheduled planning session haunt job {job_id} for {when}")
        return job_id

    except Exception as e:
        logger.error(
            f"Failed to schedule planning session haunt job for session {session_id}: {e}"
        )
        raise


def schedule_user_haunt(session_id: int, when: datetime) -> str:
    """
    Schedule a haunt_user job for escalating reminders.

    Creates a scheduled job that will trigger haunt_user() function to send
    escalating reminders with exponential backoff until session is complete.

    Args:
        session_id: The planning session ID to create reminder for.
        when: When to send the reminder (absolute datetime).

    Returns:
        The job ID for the scheduled job, format: "haunt_user_{session_id}".

    Raises:
        Exception: If job scheduling fails.

    Example:
        >>> from datetime import datetime, timedelta
        >>> next_haunt_time = datetime.utcnow() + timedelta(minutes=10)
        >>> job_id = schedule_user_haunt(42, next_haunt_time)
        >>> print(job_id)  # "haunt_user_42"
    """
    scheduler_instance = get_scheduler()

    job_id = f"haunt_user_{session_id}"

    try:
        # Remove existing job if it exists
        try:
            scheduler_instance.remove_job(job_id)
            logger.debug(f"Removed existing haunt_user job {job_id}")
        except Exception:
            pass  # Job doesn't exist, that's fine

        # Schedule new job pointing to haunt_user function
        scheduler_instance.add_job(
            func="productivity_bot.haunter_bot:haunt_user",
            trigger="date",
            run_date=when,
            args=[session_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,  # 5 minutes grace period
        )

        logger.info(f"Scheduled haunt_user job {job_id} for {when}")
        return job_id

    except Exception as e:
        logger.error(f"Failed to schedule haunt_user job for session {session_id}: {e}")
        raise


def cancel_user_haunt(session_id: int) -> bool:
    """
    Cancel a scheduled haunt_user job.

    Args:
        session_id: The planning session ID to cancel reminder for.

    Returns:
        True if job was cancelled, False if job didn't exist.

    Example:
        >>> cancelled = cancel_user_haunt(42)
        >>> print(f"Job cancelled: {cancelled}")
    """
    scheduler_instance = get_scheduler()
    job_id = f"haunt_user_{session_id}"

    try:
        scheduler_instance.remove_job(job_id)
        logger.info(f"Cancelled haunt_user job {job_id}")
        return True
    except Exception:
        logger.debug(f"No haunt_user job {job_id} to cancel")
        return False


def schedule_haunt(session_id: int, when: datetime, attempt: int = 1) -> str:
    """
    Schedule a haunt job for a user planning session with attempt tracking.

    Creates a scheduled job that will trigger the haunter bot to send
    a reminder about a planning session, including back-off attempt tracking.

    Args:
        session_id: The planning session ID to create reminder for.
        when: When to send the reminder (absolute datetime).
        attempt: The attempt number for back-off tracking (default: 1).

    Returns:
        The job ID for the scheduled job, format: "haunt_user_{session_id}_{attempt}".

    Raises:
        Exception: If job scheduling fails.

    Example:
        >>> from datetime import datetime, timedelta
        >>> session_time = datetime.utcnow() + timedelta(minutes=15)
        >>> job_id = schedule_haunt(42, session_time, attempt=1)
        >>> print(job_id)  # "haunt_user_42_1"
    """
    scheduler_instance = get_scheduler()

    job_id = f"haunt_user_{session_id}_{attempt}"

    try:
        # Remove existing job if it exists
        try:
            scheduler_instance.remove_job(job_id)
            logger.debug(f"Removed existing job {job_id}")
        except Exception:
            pass  # Job doesn't exist, that's fine

        # Schedule new job
        scheduler_instance.add_job(
            func="productivity_bot.haunter_bot:haunt_user",
            trigger="date",
            run_date=when,
            args=[session_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,  # 5 minutes grace period
        )

        logger.info(f"Scheduled user haunt job {job_id} for {when}")
        return job_id

    except Exception as e:
        logger.error(f"Failed to schedule user haunt job for session {session_id}: {e}")
        raise


def cancel_event_haunt(event_id: str) -> bool:
    """
    Cancel a scheduled haunt job for an event.

    Removes a previously scheduled reminder job for a calendar event.
    Safe to call even if the job doesn't exist.

    Args:
        event_id: The calendar event ID whose reminder should be cancelled.

    Returns:
        True if job was successfully cancelled, False if job didn't exist
        or had already completed.

    Example:
        >>> cancelled = cancel_event_haunt("cal_123")
        >>> if cancelled:
        ...     print("Reminder cancelled")
        ... else:
        ...     print("No reminder found to cancel")
    """
    scheduler_instance = get_scheduler()
    job_id = f"haunt_event_{event_id}"

    try:
        scheduler_instance.remove_job(job_id)
        logger.info(f"Cancelled haunt job {job_id}")
        return True
    except Exception:
        logger.debug(f"Job {job_id} not found (may have already run or been cancelled)")
        return False


def cancel_planning_session_haunt(session_id: int) -> bool:
    """
    Cancel a scheduled haunt job for a planning session.

    Removes a previously scheduled reminder job for a planning session.
    Safe to call even if the job doesn't exist.

    Args:
        session_id: The planning session ID whose reminder should be cancelled.

    Returns:
        True if job was successfully cancelled, False if job didn't exist
        or had already completed.

    Example:
        >>> cancelled = cancel_planning_session_haunt(42)
        >>> if cancelled:
        ...     print("Planning reminder cancelled")
        ... else:
        ...     print("No planning reminder found to cancel")
    """
    scheduler_instance = get_scheduler()
    job_id = f"haunt_session_{session_id}"

    try:
        scheduler_instance.remove_job(job_id)
        logger.info(f"Cancelled planning session haunt job {job_id}")
        return True
    except Exception:
        logger.debug(f"Job {job_id} not found (may have already run or been cancelled)")
        return False


def get_scheduled_jobs() -> List[Job]:
    """
    Get list of all scheduled jobs.

    Returns all jobs currently known to the scheduler, including
    pending, running, and paused jobs.

    Returns:
        List of APScheduler Job objects with scheduling information.

    Example:
        >>> jobs = get_scheduled_jobs()
        >>> for job in jobs:
        ...     print(f"Job {job.id} next runs at {job.next_run_time}")
    """
    scheduler_instance = get_scheduler()
    return scheduler_instance.get_jobs()


def get_job_info(job_id: str) -> Dict[str, Any]:
    """
    Get information about a specific job.

    Retrieves detailed information about a scheduled job including
    its function, trigger, timing, and arguments.

    Args:
        job_id: The unique identifier for the job.

    Returns:
        Dictionary containing job information with keys:
        - id: Job identifier
        - func: Function to be executed
        - trigger: Trigger type and configuration
        - next_run_time: When the job will next execute
        - args: Positional arguments for the function
        - kwargs: Keyword arguments for the function
        Returns empty dict if job not found.

    Example:
        >>> info = get_job_info("haunt_event_cal_123")
        >>> if info:
        ...     print(f"Next run: {info['next_run_time']}")
        ... else:
        ...     print("Job not found")
    """
    scheduler_instance = get_scheduler()

    try:
        job = scheduler_instance.get_job(job_id)
        if job:
            return {
                "id": job.id,
                "func": str(job.func),
                "trigger": str(job.trigger),
                "next_run_time": job.next_run_time,
                "args": job.args,
                "kwargs": job.kwargs,
            }
        else:
            return {}
    except Exception:
        return {}
