"""
Base Haunter Infrastructure & Back-off Engine

This module provides the abstract base class for all haunting agents,
with common APScheduler, Slack messaging, and exponential back-off utilities.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any, Callable, Optional, Union
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from ..actions.haunt_payload import HauntPayload
from ..actions.planner_action import PlannerAction, get_planner_system_message
from ..common import get_logger
from ..database import get_db_session
from ..models import PlanningSession

logger = get_logger("base_haunter")


class BaseHaunter(ABC):
    """
    Abstract base class for all haunting agents.

    Provides common infrastructure for:
    - APScheduler job management
    - Slack messaging (send, schedule, delete)
    - Exponential back-off logic
    - Intent parsing using PlannerAction schema
    - Abstract handoff to PlanningAgent

    Each concrete haunter implements persona-specific logic while
    reusing this common foundation.
    """

    def __init__(
        self,
        session_id: Union[int, UUID],
        slack: AsyncWebClient,
        scheduler: AsyncIOScheduler,
    ):
        """
        Initialize base haunter with required dependencies.

        Args:
            session_id: Unique identifier for the haunting session
            slack: Slack web client for messaging
            scheduler: APScheduler instance for job scheduling
        """
        self.session_id = session_id
        self.slack = slack
        self.scheduler = scheduler
        self.logger = get_logger(self.__class__.__name__)

        # Track active jobs for cleanup
        self._active_jobs: set[str] = set()

        # Back-off configuration (can be overridden by subclasses)
        self.backoff_base_minutes = 5
        self.backoff_cap_minutes = 120

    # ========================================================================
    # APScheduler Helpers
    # ========================================================================

    def schedule_job(
        self, job_id: str, run_dt: datetime, fn: Callable, *args, **kwargs
    ) -> bool:
        """
        Schedule a job with APScheduler.

        Args:
            job_id: Unique identifier for the job
            run_dt: When to run the job
            fn: Function to execute
            *args: Arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            True if job was scheduled successfully, False otherwise
        """
        try:
            # Cancel existing job with same ID
            self.cancel_job(job_id)

            # Schedule new job
            self.scheduler.add_job(
                fn,
                trigger="date",
                run_date=run_dt,
                args=args,
                kwargs=kwargs,
                id=job_id,
                replace_existing=True,
            )

            # Track for cleanup
            self._active_jobs.add(job_id)

            self.logger.info(f"Scheduled job {job_id} for {run_dt}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to schedule job {job_id}: {e}")
            return False

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a scheduled job.

        Args:
            job_id: Unique identifier for the job to cancel

        Returns:
            True if job was cancelled, False if job didn't exist
        """
        try:
            if self.scheduler.get_job(job_id):
                self.scheduler.remove_job(job_id)
                self._active_jobs.discard(job_id)
                self.logger.info(f"Cancelled job {job_id}")
                return True
            else:
                self.logger.debug(f"Job {job_id} doesn't exist, nothing to cancel")
                return False

        except Exception as e:
            self.logger.error(f"Failed to cancel job {job_id}: {e}")
            return False

    def cleanup_all_jobs(self) -> None:
        """Cancel all jobs managed by this haunter."""
        jobs_to_cancel = list(self._active_jobs)
        for job_id in jobs_to_cancel:
            self.cancel_job(job_id)

    # ========================================================================
    # Slack Messaging Helpers
    # ========================================================================

    async def send(
        self,
        text: str,
        *,
        channel: str,
        thread_ts: Optional[str] = None,
        blocks: Optional[list] = None,
    ) -> Optional[str]:
        """
        Send a Slack message immediately.

        Args:
            text: Message text
            channel: Slack channel or user ID
            thread_ts: Optional thread timestamp to reply in thread
            blocks: Optional Slack blocks for rich formatting

        Returns:
            Message timestamp if successful, None otherwise
        """
        try:
            response = await self.slack.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts,
                username="👻 HaunterBot",
                icon_emoji=":ghost:",
            )

            message_ts = response.get("ts")
            self.logger.info(f"Sent message to {channel}, ts={message_ts}")
            return message_ts

        except Exception as e:
            self.logger.error(f"Failed to send message to {channel}: {e}")
            return None

    async def schedule_slack(
        self,
        text: str,
        post_at: datetime,
        *,
        channel: str,
        thread_ts: Optional[str] = None,
    ) -> Optional[str]:
        """
        Schedule a Slack message for future delivery and persist the ID.

        Args:
            text: Message text
            post_at: When to send the message
            channel: Slack channel or user ID
            thread_ts: Optional thread timestamp to reply in thread

        Returns:
            Scheduled message ID if successful, None otherwise
        """
        try:
            # Import here to avoid circular imports
            from ..slack_utils import schedule_dm

            # Schedule the message
            post_at_unix = int(post_at.timestamp())
            scheduled_id = await schedule_dm(
                self.slack, channel, text, post_at_unix, thread_ts
            )

            # Persist the scheduled ID in the database
            async with get_db_session() as db:
                if isinstance(self.session_id, int):
                    session_query = select(PlanningSession).where(
                        PlanningSession.id == self.session_id
                    )
                else:
                    try:
                        session_id_int = int(self.session_id)
                        session_query = select(PlanningSession).where(
                            PlanningSession.id == session_id_int
                        )
                    except (ValueError, TypeError):
                        self.logger.warning(
                            f"Cannot convert session_id {self.session_id} to int"
                        )
                        return scheduled_id

                result = await db.execute(session_query)  # type: ignore
                session = result.scalar_one_or_none()

                if session:
                    if not session.slack_sched_ids:
                        session.slack_sched_ids = []
                    session.slack_sched_ids.append(scheduled_id)
                    # Note: db.commit() is automatic with get_db_session() context manager
                    self.logger.info(
                        f"Persisted scheduled message ID {scheduled_id} for session {self.session_id}"
                    )
                else:
                    self.logger.warning(f"No session found with ID {self.session_id}")

            return scheduled_id

        except Exception as e:
            self.logger.error(f"Failed to schedule and persist message: {e}")
            return None

    async def delete_scheduled(self, scheduled_id: str, channel: str) -> bool:
        """
        Delete a scheduled Slack message.

        Args:
            scheduled_id: ID of the scheduled message
            channel: Slack channel where message was scheduled

        Returns:
            True if message was deleted, False otherwise
        """
        try:
            await self.slack.chat_deleteScheduledMessage(
                channel=channel, scheduled_message_id=scheduled_id
            )

            self.logger.info(f"Deleted scheduled message {scheduled_id}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to delete scheduled message {scheduled_id}: {e}")
            return False

    # ========================================================================
    # Exponential Back-off Logic
    # ========================================================================

    def next_delay(self, attempt: int) -> int:
        """
        Calculate next delay using exponential back-off.

        Base implementation: 5, 10, 20, 40, 80, 120, 120... minutes
        Subclasses can override for persona-specific timing.

        Args:
            attempt: Attempt number (0-based)

        Returns:
            Delay in minutes
        """
        if attempt <= 0:
            return self.backoff_base_minutes

        # Exponential: 5 * 2^attempt, capped at backoff_cap_minutes
        delay = self.backoff_base_minutes * (2**attempt)
        return min(delay, self.backoff_cap_minutes)

    def next_run_time(self, attempt: int) -> datetime:
        """
        Calculate next run time based on attempt number.

        Args:
            attempt: Attempt number (0-based)

        Returns:
            Datetime for next execution
        """
        delay_minutes = self.next_delay(attempt)
        return datetime.utcnow() + timedelta(minutes=delay_minutes)

    # ========================================================================
    # Intent Parsing
    # ========================================================================

    async def parse_intent(self, text: str) -> PlannerAction:
        """
        Parse user text into a PlannerAction using LLM.

        This uses the existing PlannerAction schema and system prompt
        to extract structured intent from user replies.

        Args:
            text: User's raw text input

        Returns:
            PlannerAction with parsed intent
        """
        try:
            # Import OpenAI client (lazy import to avoid circular dependencies)
            from openai import AsyncOpenAI

            from ..common import get_config

            config = get_config()
            client = AsyncOpenAI(api_key=config.openai_api_key)

            # Use existing PlannerAction system prompt
            system_message = get_planner_system_message()

            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=200,
            )

            # Parse JSON response into PlannerAction
            json_content = response.choices[0].message.content
            if json_content:
                json_content = json_content.strip()
                import json

                action_data = json.loads(json_content)

                return PlannerAction(**action_data)
            else:
                self.logger.error("Empty response from LLM")
                return PlannerAction(action="unknown")

        except Exception as e:
            self.logger.error(f"Failed to parse intent from '{text}': {e}")
            # Return unknown action as fallback
            return PlannerAction(action="unknown")

    async def generate_message(self, context: str, attempt: int = 1) -> str:
        """
        Generate LLM-powered message based on context and attempt number.

        Args:
            context: Context for message generation (e.g., "initial_bootstrap", "followup_reminder", "event_start")
            attempt: Current attempt number for escalation/variety

        Returns:
            Generated message string
        """
        try:
            # Import OpenAI client (lazy import to avoid circular dependencies)
            from openai import AsyncOpenAI

            from ..common import get_config

            config = get_config()
            client = AsyncOpenAI(api_key=config.openai_api_key)

            # Get haunter-specific system prompt for message generation
            system_prompt = self._get_message_system_prompt(context, attempt)

            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"Generate a {context} message for attempt #{attempt}",
                    },
                ],
                temperature=0.7,  # Higher temperature for message variety
                max_tokens=500,
            )

            message = response.choices[0].message.content
            if message:
                return message.strip()
            else:
                self.logger.error(f"Empty response from LLM for context '{context}'")
                return f"Hi! I'm here to help with your planning session. (attempt {attempt})"

        except Exception as e:
            self.logger.error(
                f"Failed to generate message for context '{context}': {e}"
            )
            # Return basic fallback message
            return (
                f"Hi! I'm here to help with your planning session. (attempt {attempt})"
            )

    def _get_message_system_prompt(self, context: str, attempt: int) -> str:
        """
        Get haunter-specific system prompt for message generation.

        Override this method in concrete haunters to provide context-specific prompts.

        Args:
            context: Message context (e.g., "initial_bootstrap", "followup_reminder")
            attempt: Current attempt number

        Returns:
            System prompt for message generation
        """
        # Default base prompt - concrete haunters should override this
        return f"""You are a helpful productivity assistant. Generate a friendly, encouraging message for context: {context}.
        
This is attempt #{attempt}. Make the message:
- Natural and conversational
- Encouraging but not pushy
- Focused on helping with productivity planning
- Appropriate for the attempt number (more urgent if higher attempts)

Keep the message concise (1-3 sentences) and include relevant emojis."""

    # ========================================================================
    # Abstract Handoff Interface
    # ========================================================================

    @abstractmethod
    async def _route_to_planner(self, intent: Any) -> bool:
        """
        Route parsed intent to PlanningAgent for calendar operations.

        This method must be implemented by each concrete haunter to handle
        handoff to PlanningAgent based on the parsed user intent.

        Args:
            intent: Parsed action from user input (schema varies by haunter type)

        Returns:
            True if handoff was successful, False otherwise
        """
        pass

    @abstractmethod
    async def handle_user_reply(self, text: str, attempt: int = 0) -> bool:
        """
        Handle user reply and route to appropriate action.

        This method must be implemented by each concrete haunter to parse
        user input and take appropriate action based on their persona.

        Args:
            text: User's reply text
            attempt: Current attempt number for follow-up tracking

        Returns:
            True if reply was handled successfully, False otherwise
        """
        pass

    # ========================================================================
    # Cleanup Methods
    # ========================================================================

    async def _stop_reminders(self) -> None:
        """
        Cancel all APScheduler jobs + Slack scheduled DMs for this session.

        This method should be called when:
        - User replies with mark_done
        - PlanningAgent marks session COMPLETE
        - CommitmentHaunter receives mark_done
        """
        try:
            # Import here to avoid circular imports
            from ..slack_utils import delete_scheduled

            # Cancel APScheduler jobs
            jobs_cancelled = 0
            for job in self.scheduler.get_jobs():
                if job.id.startswith(f"{self.session_id}-") or job.id.startswith(
                    f"haunt_{self.session_id}"
                ):
                    self.scheduler.remove_job(job.id)
                    jobs_cancelled += 1

            # Also cancel jobs tracked in _active_jobs
            for job_id in list(self._active_jobs):
                self.cancel_job(job_id)

            self.logger.info(
                f"Cancelled {jobs_cancelled} scheduler jobs for session {self.session_id}"
            )

            # Delete Slack scheduled messages
            async with get_db_session() as db:
                # Query for the session (session_id might be int, so try both)
                if isinstance(self.session_id, int):
                    session_query = select(PlanningSession).where(
                        PlanningSession.id == self.session_id
                    )
                else:
                    # If session_id is UUID string, need to convert to int or find by different field
                    # For now, assume session_id is the primary key (int)
                    try:
                        session_id_int = int(self.session_id)
                        session_query = select(PlanningSession).where(
                            PlanningSession.id == session_id_int
                        )
                    except (ValueError, TypeError):
                        self.logger.warning(
                            f"Cannot convert session_id {self.session_id} to int"
                        )
                        return

                result = await db.execute(session_query)  # type: ignore
                session = result.scalar_one_or_none()

                if session and session.slack_sched_ids:
                    deleted_count = 0

                    # Get channel from session or use default
                    channel = getattr(self, "channel", session.channel_id)
                    if not channel:
                        self.logger.warning(
                            f"No channel found for session {self.session_id}, cannot delete scheduled messages"
                        )
                        return

                    for sched_id in session.slack_sched_ids:
                        success = await delete_scheduled(self.slack, channel, sched_id)
                        if success:
                            deleted_count += 1

                    # Clear the scheduled IDs
                    session.slack_sched_ids = []
                    # Note: db.commit() is automatic with get_db_session() context manager

                    self.logger.info(
                        f"Deleted {deleted_count} scheduled Slack messages for session {self.session_id}"
                    )

        except Exception as e:
            self.logger.error(
                f"Error stopping reminders for session {self.session_id}: {e}"
            )

    # ========================================================================
    # Convenience Methods
    # ========================================================================

    def _job_id(self, job_type: str, attempt: int = 0) -> str:
        """Generate consistent job ID for this haunter."""
        return f"haunt_{self.session_id}_{job_type}_{attempt}"

    def _schedule_followup(self, attempt: int, job_type: str = "followup") -> bool:
        """
        Schedule next follow-up reminder with exponential back-off.

        Args:
            attempt: Current attempt number
            job_type: Type of follow-up job

        Returns:
            True if follow-up was scheduled successfully
        """
        next_run = self.next_run_time(attempt)
        job_id = self._job_id(job_type, attempt + 1)

        return self.schedule_job(
            job_id=job_id,
            run_dt=next_run,
            fn=self._send_followup_reminder,
            attempt=attempt + 1,
        )

    async def _send_followup_reminder(self, attempt: int) -> None:
        """
        Default follow-up reminder implementation.
        Subclasses should override for persona-specific messaging.
        """
        self.logger.info(f"Follow-up reminder #{attempt} for session {self.session_id}")
        # Subclasses implement actual messaging logic
