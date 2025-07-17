"""
Commitment Haunter

Handles reminders for users who have committed to specific planning times.
Focuses on completion tracking and follow-through.
"""

import logging
from datetime import datetime
from typing import Optional, Union
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from ...database import get_db_session
from ...models import PlanningSession, PlanStatus
from ..base_haunter import BaseHaunter
from .action import COMMITMENT_PROMPT, CommitmentAction


class CommitmentHaunter(BaseHaunter):
    """
    Commitment haunter for users who've committed to planning times.

    Handles follow-through on scheduled planning sessions with a focus
    on accountability and completion tracking. Uses a supportive but
    persistent tone to help users follow through on commitments.
    """

    def __init__(
        self,
        session_id: Union[int, UUID],
        slack: AsyncWebClient,
        scheduler: AsyncIOScheduler,
        channel: str,
        thread_ts: Optional[str] = None,
        planned_time: Optional[datetime] = None,
    ):
        """
        Initialize commitment haunter.

        Args:
            session_id: Unique identifier for the haunting session
            slack: Slack web client for messaging
            scheduler: APScheduler instance for job scheduling
            channel: Slack channel or user ID to haunt
            thread_ts: Optional thread timestamp for threaded replies
            planned_time: The specific time the user committed to planning
        """
        super().__init__(session_id, slack, scheduler)
        self.channel = channel
        self.thread_ts = thread_ts
        self.planned_time = planned_time

        # Commitment-specific configuration
        self.backoff_base_minutes = 10  # Shorter intervals for committed users
        self.backoff_cap_minutes = 120  # 2 hours max

    async def parse_intent(self, text: str) -> CommitmentAction:
        """
        Parse user text into a CommitmentAction using LLM.

        Overrides base implementation to use CommitmentAction schema
        and commitment-specific system prompt.

        Args:
            text: User's raw text input

        Returns:
            CommitmentAction with parsed intent
        """
        try:
            # Import OpenAI client (lazy import to avoid circular dependencies)
            from openai import AsyncOpenAI

            from ...common import get_config

            config = get_config()
            client = AsyncOpenAI(api_key=config.openai_api_key)

            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": COMMITMENT_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=200,
            )

            # Parse JSON response into CommitmentAction
            json_content = response.choices[0].message.content
            if json_content:
                json_content = json_content.strip()
                import json

                action_data = json.loads(json_content)
                return CommitmentAction(**action_data)
            else:
                self.logger.error("Empty response from LLM")
                return CommitmentAction(action="unknown")

        except Exception as e:
            self.logger.error(f"Failed to parse commitment intent from '{text}': {e}")
            return CommitmentAction(action="unknown")

    async def _route_to_planner(self, intent: CommitmentAction) -> bool:
        """
        Route parsed intent to PlanningAgent for calendar operations.

        Args:
            intent: Parsed CommitmentAction from user input

        Returns:
            True if handoff was successful, False otherwise
        """
        try:
            # Import router agent for handoff
            from ...actions.haunt_payload import HauntPayload
            from ...agents.router_agent import RouterAgent

            # Create structured payload for handoff
            payload = HauntPayload(
                session_id=(
                    UUID(str(self.session_id))
                    if isinstance(self.session_id, int)
                    else self.session_id
                ),
                action=intent.action,
                minutes=intent.minutes,
                commit_time_str=None,  # CommitmentAction doesn't have commit_time_str
            )

            # Route to planning agent
            router = RouterAgent()
            result = await router.route_payload(payload)

            if result:
                self.logger.info(
                    f"Successfully routed commitment intent to planner: {intent.action}"
                )
                return True
            else:
                self.logger.error(f"Failed to route commitment intent: {intent.action}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to route commitment intent to planner: {e}")
            return False

    async def handle_user_reply(self, text: str, attempt: int = 0) -> bool:
        """
        Handle user reply and route to appropriate action.

        Args:
            text: User's reply text
            attempt: Current attempt number for follow-up tracking

        Returns:
            True if reply was handled successfully, False otherwise
        """
        try:
            # Parse user intent
            intent = await self.parse_intent(text)

            self.logger.info(f"Parsed commitment intent: {intent.action}")

            # Handle based on action type
            if intent.action == "mark_done":
                # User completed their planning session
                completion_message = await self.generate_message("completion_celebration", 1)
                await self.send(
                    completion_message,
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
                # Cancel any pending reminders since session is complete
                self.cleanup_all_jobs()
                return True

            elif intent.action == "postpone":
                # Route to planning agent for rescheduling
                success = await self._route_to_planner(intent)

                if success:
                    reschedule_success_message = await self.generate_message("reschedule_success", 1)
                    await self.send(
                        reschedule_success_message,
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    return True
                else:
                    # Schedule follow-up with exponential back-off
                    success = self._schedule_followup(attempt, "commitment_followup")

                    if success:
                        delay = self.next_delay(attempt + 1)
                        reschedule_retry_message = await self.generate_message("reschedule_retry", 1)
                        await self.send(
                            reschedule_retry_message,
                            channel=self.channel,
                            thread_ts=self.thread_ts,
                        )
                        return True
                    else:
                        reschedule_failed_message = await self.generate_message("reschedule_failed", 1)
                        await self.send(
                            reschedule_failed_message,
                            channel=self.channel,
                            thread_ts=self.thread_ts,
                        )
                        return False

            else:  # unknown action
                # Try to clarify with user
                clarification_message = await self.generate_message("clarification_request", 1)
                await self.send(
                    clarification_message,
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to handle commitment user reply: {e}")
            error_message = await self.generate_message("error_response", 1)
            await self.send(
                error_message,
                channel=self.channel,
                thread_ts=self.thread_ts,
            )
            return False

    async def send_commitment_reminder(self) -> Optional[str]:
        """
        Send reminder about committed planning session.

        Returns:
            Message timestamp if successful, None otherwise
        """
        # Generate LLM-powered message instead of hardcoded template
        message = await self.generate_message("commitment_reminder", 1)

        return await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

    async def _send_followup_reminder(self, attempt: int) -> None:
        """
        Send commitment-specific follow-up reminder.

        Args:
            attempt: Current attempt number
        """
        # Generate LLM-powered message instead of cycling through hardcoded ones
        message = await self.generate_message("commitment_followup", attempt)

        await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

        self.logger.info(
            f"Sent commitment follow-up #{attempt} for session {self.session_id}"
        )

    async def send_pre_session_reminder(
        self, minutes_before: int = 15
    ) -> Optional[str]:
        """
        Send a reminder before the planned session time.

        Args:
            minutes_before: How many minutes before the session to remind

        Returns:
            Message timestamp if successful, None otherwise
        """
        if not self.planned_time:
            return None

        # Generate LLM-powered reminder message
        message = await self.generate_message("pre_session_reminder", 1)

        return await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

    # ========================================================================
    # Event-Start Haunting
    # ========================================================================

    async def start_event_haunt(self):
        """Start haunting when the event begins."""
        try:
            # Generate and send event-start message
            event_start_message = await self.generate_message("event_start", 1)
            message_ts = await self.send(
                event_start_message,
                channel=self.channel,
                thread_ts=self.thread_ts,
            )
            
            if message_ts:
                self.logger.info(f"Sent event start message for session {self.session_id}: {message_ts}")
                # Schedule timeout check using commitment-specific timing
                from datetime import datetime, timedelta
                timeout_check_time = datetime.now() + timedelta(minutes=15)  # 15 min grace period
                timeout_message = await self.generate_message("timeout_check", 2)
                
                await self.schedule_slack(
                    text=timeout_message,
                    post_at=timeout_check_time,
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
            else:
                self.logger.error(f"Failed to send event start message for session {self.session_id}")
                
        except Exception as e:
            self.logger.error(f"Failed to start event haunt for session {self.session_id}: {e}")

    async def _check_started_timeout(self):
        """Check if session is still incomplete after timeout."""
        try:
            # Check current session status from database
            async with get_db_session() as db:
                result = await db.execute(
                    select(PlanningSession).where(PlanningSession.id == self.session_id)
                )
                session = result.scalar_one_or_none()
                
                if session and session.status == PlanStatus.NOT_STARTED:
                    # Session hasn't started yet - send follow-up
                    timeout_message = await self.generate_message("timeout_followup", 3)
                    await self.send(
                        timeout_message,
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    self.logger.info(f"Sent timeout followup for session {self.session_id}")
                else:
                    self.logger.info(f"Session {self.session_id} status changed, no timeout followup needed")
                    
        except Exception as e:
            self.logger.error(f"Failed to check started timeout for session {self.session_id}: {e}")

    def _get_message_system_prompt(self, context: str, attempt: int) -> str:
        """
        Get commitment-specific system prompt for message generation.
        
        Args:
            context: Message context 
            attempt: Current attempt number
            
        Returns:
            Commitment-specific system prompt
        """
        if context == "commitment_reminder":
            return """You are an encouraging productivity assistant following up about a committed planning session.

Generate a friendly check-in message that:
- Asks how their committed planning session went
- Offers options for completion status (complete, postpone, etc.)
- Shows accountability support without being pushy
- Uses encouraging emojis (⏰, ✅, 📅, etc.)

Keep it supportive and focused on helping them stay on track."""

        elif context == "commitment_followup":
            if attempt <= 2:
                urgency = "gentle and encouraging"
            elif attempt <= 4:
                urgency = "more direct but supportive"
            else:
                urgency = "persistent but understanding"
                
            return f"""You are an encouraging productivity assistant doing follow-up #{attempt} about a planning session commitment.

Generate a {urgency} follow-up message that:
- Continues to check on their planning session progress
- Shows persistence appropriate for attempt #{attempt}
- Maintains accountability support without guilt
- Uses appropriate emojis for the follow-up level

Keep it balanced between accountability and understanding."""

        elif context == "pre_session_reminder":
            return """You are a helpful productivity assistant sending a pre-session reminder.

Generate an anticipatory reminder message that:
- Alerts them their planning session is starting soon
- Builds excitement and readiness for the session
- Briefly highlights what they can accomplish
- Uses motivating emojis (🔔, ⏰, 🎯, etc.)

Keep it energizing and focused on preparation."""

        elif context == "completion_celebration":
            return """You are an enthusiastic productivity assistant celebrating a completed planning session.

Generate a celebratory message that:
- Congratulates them on completing their planning session
- Acknowledges their commitment follow-through
- Briefly mentions future support
- Uses celebratory emojis (🎉, ✅, 🌟, etc.)

Keep it positive and encouraging."""

        elif context == "reschedule_success":
            return """You are a supportive productivity assistant confirming successful rescheduling.

Generate a confirmation message that:
- Confirms the rescheduling was successful
- Shows understanding that timing changes happen
- Maintains positive tone about their planning commitment
- Uses supportive emojis (✅, 📅, 👍, etc.)

Keep it reassuring and supportive."""

        elif context == "reschedule_retry":
            return """You are a helpful productivity assistant explaining a rescheduling issue.

Generate a brief message that:
- Explains there was a technical difficulty with rescheduling
- Reassures them you'll try again soon
- Maintains helpful tone despite the issue
- Uses appropriate emojis (🔄, ⏰, etc.)

Keep it brief and reassuring."""

        elif context == "reschedule_failed":
            return """You are a understanding productivity assistant handling a rescheduling failure.

Generate a brief message that:
- Acknowledges the rescheduling attempt didn't work
- Offers to try again later
- Maintains supportive tone
- Uses appropriate emojis (🤝, ⏰, etc.)

Keep it understanding and brief."""

        elif context == "clarification_request":
            return """You are a helpful productivity assistant asking for clarification about a planning session.

Generate a clarifying message that:
- Acknowledges you didn't understand their response
- Asks specifically about completion or postponement
- Offers clear options for their response
- Uses friendly emojis (❓, 💭, etc.)

Keep it clear and helpful."""

        elif context == "error_response":
            return """You are a apologetic productivity assistant handling a technical error.

Generate a brief error message that:
- Apologizes for the technical issue
- Assures them you'll try again later
- Maintains professional tone
- Uses appropriate emojis (😅, 🔧, etc.)

Keep it brief and professional."""

        elif context == "event_start":
            return """You are an encouraging productivity assistant helping someone who has committed to a planning session.

Generate an energetic, motivational message that:
- Acknowledges their planning session is starting now
- Celebrates their commitment to planning
- Encourages them to begin with focus and intention
- Offers gentle guidance on how to make the most of the session
- Uses positive, energizing emojis (🎯, ⚡, 🔥, 💪, etc.)

Keep it motivational and action-oriented without being overwhelming."""

        elif context == "timeout_check":
            return """You are a helpful productivity assistant checking in during a planning session.

Generate a gentle check-in message that:
- Acknowledges their planning session should have started
- Offers encouragement if they're running behind
- Provides gentle guidance to help them get started
- Maintains supportive tone without being pushy
- Uses encouraging emojis (⏰, 💙, 🌟, etc.)

Keep it supportive and understanding."""

        elif context == "timeout_followup":
            return """You are a caring productivity assistant following up on a planning session that hasn't started yet.

Generate a supportive follow-up message that:
- Acknowledges the session time has passed
- Offers flexible options (start now, reschedule, etc.)
- Maintains encouraging tone without guilt
- Shows understanding that life happens
- Uses warm, supportive emojis (💙, 🤗, 📅, etc.)

Keep it understanding and solution-focused."""

        else:
            # Fallback to base implementation
            return super()._get_message_system_prompt(context, attempt)
