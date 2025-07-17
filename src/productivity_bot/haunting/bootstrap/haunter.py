"""
Bootstrap Haunter

Handles initial planning event creation for new users or first-time planners.
Focuses on creating calendar events and basic postponement actions.
"""

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Optional, Union
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from ...database import get_db_session
from ...models import PlanningSession, PlanStatus
from ..base_haunter import BaseHaunter
from .action import BOOTSTRAP_PROMPT, BootstrapAction


class PlanningBootstrapHaunter(BaseHaunter):
    """
    Bootstrap haunter for initial planning event creation.

    Handles new users or first-time planners who need guidance
    in creating their first planning events. Uses a supportive,
    encouraging tone while being direct about action needs.
    """

    def __init__(
        self,
        session_id: Union[int, UUID],
        slack: AsyncWebClient,
        scheduler: AsyncIOScheduler,
        channel: str,
        thread_ts: Optional[str] = None,
    ):
        """
        Initialize bootstrap haunter.

        Args:
            session_id: Unique identifier for the haunting session
            slack: Slack web client for messaging
            scheduler: APScheduler instance for job scheduling
            channel: Slack channel or user ID to haunt
            thread_ts: Optional thread timestamp for threaded replies
        """
        super().__init__(session_id, slack, scheduler)
        self.channel = channel
        self.thread_ts = thread_ts

        # Bootstrap-specific configuration
        self.backoff_base_minutes = 15  # Slightly longer for first-time users
        self.backoff_cap_minutes = 240  # 4 hours max

    async def parse_intent(self, text: str) -> BootstrapAction:
        """
        Parse user text into a BootstrapAction using LLM.

        Overrides base implementation to use BootstrapAction schema
        and bootstrap-specific system prompt.

        Args:
            text: User's raw text input

        Returns:
            BootstrapAction with parsed intent
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
                    {"role": "system", "content": BOOTSTRAP_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=200,
            )

            # Parse JSON response into BootstrapAction
            json_content = response.choices[0].message.content
            if json_content:
                json_content = json_content.strip()
                import json

                action_data = json.loads(json_content)
                return BootstrapAction(**action_data)
            else:
                self.logger.error("Empty response from LLM")
                return BootstrapAction(action="unknown")

        except Exception as e:
            self.logger.error(f"Failed to parse bootstrap intent from '{text}': {e}")
            return BootstrapAction(action="unknown")

    async def _route_to_planner(self, intent: BootstrapAction) -> bool:
        """
        Route parsed intent to PlanningAgent for calendar operations.

        Args:
            intent: Parsed BootstrapAction from user input

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
                commit_time_str=intent.commit_time_str,
            )

            # Route to planning agent
            router = RouterAgent()
            result = await router.route_payload(payload)

            if result:
                self.logger.info(
                    f"Successfully routed bootstrap intent to planner: {intent.action}"
                )
                return True
            else:
                self.logger.error(f"Failed to route bootstrap intent: {intent.action}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to route bootstrap intent to planner: {e}")
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

            self.logger.info(f"Parsed bootstrap intent: {intent.action}")

            # Handle based on action type
            if intent.action == "create_event":
                # Route to planning agent for calendar creation
                success = await self._route_to_planner(intent)

                if success:
                    await self.send(
                        "✅ Great! I'll help you create that planning event.",
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    # Cancel any pending reminders since user responded positively
                    self.cleanup_all_jobs()
                    return True
                else:
                    await self.send(
                        "⚠️ I had trouble creating that event. Let me try again shortly.",
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    return False

            elif intent.action == "postpone":
                # Schedule follow-up with exponential back-off
                success = self._schedule_followup(attempt, "bootstrap_followup")

                if success:
                    delay = self.next_delay(attempt + 1)
                    await self.send(
                        f"No worries! I'll check back with you in {delay} minutes.",
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    return True
                else:
                    await self.send(
                        "I'll try to follow up later.",
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    return False

            else:  # unknown action
                # Try to clarify with user
                await self.send(
                    "I'm not sure what you'd like to do. Would you like me to:\n"
                    "• Help you **create a planning event** for a specific time\n"
                    "• **Postpone** this reminder for later\n"
                    "\nJust let me know!",
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to handle bootstrap user reply: {e}")
            await self.send(
                "Sorry, I encountered an error. Let me try again later.",
                channel=self.channel,
                thread_ts=self.thread_ts,
            )
            return False

    async def send_initial_bootstrap_message(self) -> Optional[str]:
        """
        Send initial bootstrap message to get the user started.

        Returns:
            Message timestamp if successful, None otherwise
        """
        # Generate LLM-powered message instead of hardcoded template
        message = await self.generate_message("initial_bootstrap", 1)

        return await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

    async def _send_followup_reminder(self, attempt: int) -> None:
        """
        Send bootstrap-specific follow-up reminder.

        Args:
            attempt: Current attempt number
        """
        # Generate LLM-powered message instead of cycling through hardcoded ones
        message = await self.generate_message("followup_reminder", attempt)

        await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

        self.logger.info(
            f"Sent bootstrap follow-up #{attempt} for session {self.session_id}"
        )

    # ========================================================================
    # Daily Bootstrap Management
    # ========================================================================

    @classmethod
    def schedule_daily(cls):
        """Schedule the daily bootstrap check at 17:00 Amsterdam time."""
        from apscheduler.triggers.cron import CronTrigger

        from ...scheduler import get_scheduler

        sched = get_scheduler()
        if not sched.get_job("daily-planning-bootstrap"):
            sched.add_job(
                cls._run_daily_check,
                CronTrigger(hour=17, minute=0, timezone="Europe/Amsterdam"),
                id="daily-planning-bootstrap",
            )

    @classmethod
    async def _run_daily_check(cls):
        """Run the daily check for missing planning events."""
        # TODO: Get app context for slack/scheduler
        # For now, just log that we would check
        from ...common import get_logger

        logger = get_logger("bootstrap_daily")
        logger.info(
            "Daily bootstrap check triggered - would check for missing planning events"
        )

    async def _daily_check(self):
        """Check if tomorrow has a planning event, start bootstrap if not."""
        # TODO: Implement when we have proper app context
        self.logger.info("Daily check for planning events - implementation needed")

    async def _start_bootstrap_haunt(self):
        """Start the bootstrap haunting process with LLM-generated messages."""
        try:
            # Generate and send initial bootstrap message
            initial_message = await self.generate_message("initial_bootstrap", 1)
            message_ts = await self.send(
                initial_message,
                channel=self.channel,
                thread_ts=self.thread_ts,
            )
            
            if message_ts:
                self.logger.info(f"Sent initial bootstrap message: {message_ts}")
                # Schedule follow-up reminder using exponential backoff
                from datetime import datetime, timedelta
                followup_time = datetime.now() + timedelta(minutes=self.backoff_base_minutes)
                followup_message = await self.generate_message("followup_reminder", 2)
                
                await self.schedule_slack(
                    text=followup_message,
                    post_at=followup_time,
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
            else:
                self.logger.error("Failed to send initial bootstrap message")
                
        except Exception as e:
            self.logger.error(f"Failed to start bootstrap haunt: {e}")

    def _get_message_system_prompt(self, context: str, attempt: int) -> str:
        """
        Get bootstrap-specific system prompt for message generation.
        
        Args:
            context: Message context 
            attempt: Current attempt number
            
        Returns:
            Bootstrap-specific system prompt
        """
        if context == "initial_bootstrap":
            return """You are a friendly, encouraging productivity assistant helping someone set up their first planning session.

Generate a warm, welcoming message that:
- Introduces yourself as a planning assistant 
- Explains what planning sessions are and their benefits
- Offers to create a planning event on their calendar
- Gives them options to schedule now or postpone
- Uses encouraging but not pushy tone
- Includes relevant emojis (👋, 📅, ⏰, etc.)

Keep it conversational and focus on the value of planning without being overwhelming."""

        elif context == "followup_reminder":
            if attempt <= 3:
                urgency = "gentle"
            elif attempt <= 5:
                urgency = "moderate"
            else:
                urgency = "persistent"
                
            return f"""You are a friendly productivity assistant following up about scheduling a planning session.

Generate a {urgency} reminder message (attempt #{attempt}) that:
- Acknowledges they haven't responded yet
- Reiterates the value of planning sessions
- Offers flexible scheduling options
- Maintains encouraging tone appropriate for attempt #{attempt}
- Uses friendly emojis (📅, ⏰, 🎯, etc.)

Keep it brief and focused on helping them take the next step."""

        else:
            # Fallback to base implementation
            return super()._get_message_system_prompt(context, attempt)
