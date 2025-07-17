"""
Bootstrap Haunter

Handles initial planning event creation for new users or first-time planners.
Focuses on creating calendar events and basic postponement actions.
"""

import logging
from datetime import datetime
from typing import Optional, Union
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

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
        message = (
            "👋 Hi there! I'm here to help you set up your first planning session.\n\n"
            "Planning sessions are dedicated time blocks where you can:\n"
            "• Review your goals and priorities\n"
            "• Plan your week ahead\n"
            "• Reflect on what's working and what isn't\n\n"
            "Would you like me to **create a planning event** on your calendar? "
            "Just tell me when works best for you, or say **postpone** if you'd prefer to do this later."
        )

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
        messages = [
            "👻 Just checking in! Ready to set up that planning session?",
            "📅 Still thinking about that planning session? I'm here when you're ready!",
            "⏰ No pressure, but I'd love to help you get that planning time on your calendar.",
            "🎯 Planning sessions can really help with productivity - shall we schedule one?",
        ]

        # Cycle through messages based on attempt number
        message_index = (attempt - 1) % len(messages)
        message = messages[message_index]

        await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

        self.logger.info(
            f"Sent bootstrap follow-up #{attempt} for session {self.session_id}"
        )
