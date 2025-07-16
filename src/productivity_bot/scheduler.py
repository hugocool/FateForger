"""
APScheduler integration for event-driven reminders and haunting.
"""

import logging
from datetime import datetime
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor

from .common import get_config, get_logger

logger = get_logger("scheduler")

# Global scheduler instance
scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Get the global scheduler instance."""
    global scheduler
    if scheduler is None:
        scheduler = create_scheduler()
    return scheduler


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the APScheduler instance."""
    config = get_config()

    # Configure job store to persist jobs in database
    jobstore = SQLAlchemyJobStore(url=config.database_url, tablename="scheduler_jobs")

    # Configure executor for async jobs
    executor = AsyncIOExecutor()

    # Job defaults
    job_defaults = {
        "coalesce": False,
        "max_instances": 3,
        "misfire_grace_time": 300,  # 5 minutes
    }

    scheduler_instance = AsyncIOScheduler(
        jobstores={"default": jobstore},
        executors={"default": executor},
        job_defaults=job_defaults,
        timezone="UTC",
    )

    logger.info("APScheduler configured with SQLAlchemy job store")
    return scheduler_instance


async def start_scheduler():
    """Start the scheduler."""
    scheduler_instance = get_scheduler()

    if not scheduler_instance.running:
        scheduler_instance.start()
        logger.info("APScheduler started")
    else:
        logger.warning("APScheduler is already running")


async def stop_scheduler():
    """Stop the scheduler gracefully."""
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

    Args:
        event_id: The calendar event ID
        when: When to send the reminder (usually event start time minus reminder_minutes)
        reminder_minutes: How many minutes before event start to remind

    Returns:
        The job ID for the scheduled job
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
        except:
            pass  # Job doesn't exist, that's fine

        # Schedule new job
        job = scheduler_instance.add_job(
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

    Args:
        session_id: The planning session ID
        when: When to send the reminder

    Returns:
        The job ID for the scheduled job
    """
    scheduler_instance = get_scheduler()

    job_id = f"haunt_session_{session_id}"

    try:
        # Remove existing job if it exists
        try:
            scheduler_instance.remove_job(job_id)
            logger.debug(f"Removed existing job {job_id}")
        except:
            pass  # Job doesn't exist, that's fine

        # Schedule new job
        job = scheduler_instance.add_job(
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


def cancel_event_haunt(event_id: str) -> bool:
    """
    Cancel a scheduled haunt job for an event.

    Args:
        event_id: The calendar event ID

    Returns:
        True if job was cancelled, False if job didn't exist
    """
    scheduler_instance = get_scheduler()
    job_id = f"haunt_event_{event_id}"

    try:
        scheduler_instance.remove_job(job_id)
        logger.info(f"Cancelled haunt job {job_id}")
        return True
    except:
        logger.debug(f"Job {job_id} not found (may have already run or been cancelled)")
        return False


def cancel_planning_session_haunt(session_id: int) -> bool:
    """
    Cancel a scheduled haunt job for a planning session.

    Args:
        session_id: The planning session ID

    Returns:
        True if job was cancelled, False if job didn't exist
    """
    scheduler_instance = get_scheduler()
    job_id = f"haunt_session_{session_id}"

    try:
        scheduler_instance.remove_job(job_id)
        logger.info(f"Cancelled planning session haunt job {job_id}")
        return True
    except:
        logger.debug(f"Job {job_id} not found (may have already run or been cancelled)")
        return False


def get_scheduled_jobs() -> list:
    """Get list of all scheduled jobs."""
    scheduler_instance = get_scheduler()
    return scheduler_instance.get_jobs()


def get_job_info(job_id: str) -> dict:
    """Get information about a specific job."""
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
    except:
        return {}
