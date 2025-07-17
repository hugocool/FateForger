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

from ..actions.planner_action import PlannerAction, get_planner_system_message
from ..common import get_logger

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
        Schedule a Slack message for future delivery.

        Args:
            text: Message text
            post_at: When to send the message
            channel: Slack channel or user ID
            thread_ts: Optional thread timestamp to reply in thread

        Returns:
            Scheduled message ID if successful, None otherwise
        """
        try:
            # Convert datetime to Unix timestamp
            post_at_unix = int(post_at.timestamp())

            response = await self.slack.chat_scheduleMessage(
                channel=channel, text=text, post_at=post_at_unix, thread_ts=thread_ts
            )

            scheduled_id = response.get("scheduled_message_id")
            self.logger.info(f"Scheduled message {scheduled_id} for {post_at}")
            return scheduled_id

        except Exception as e:
            self.logger.error(f"Failed to schedule message: {e}")
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

    # ========================================================================
    # Abstract Handoff Interface
    # ========================================================================

    @abstractmethod
    async def _route_to_planner(self, intent: PlannerAction) -> bool:
        """
        Route parsed intent to PlanningAgent for calendar operations.

        This method must be implemented by each concrete haunter to handle
        handoff to PlanningAgent based on the parsed user intent.

        Args:
            intent: Parsed PlannerAction from user input

        Returns:
            True if handoff was successful, False otherwise
        """
        pass

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
