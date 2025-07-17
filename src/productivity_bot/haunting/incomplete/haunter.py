"""
Incomplete Haunter

Handles follow-up for users with unfinished planning sessions.
Focuses on gentle persistence and offering support to complete planning.
"""

import logging
from datetime import datetime
from typing import Optional, Union
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

from ..base_haunter import BaseHaunter
from .action import INCOMPLETE_PROMPT, IncompleteAction


class IncompletePlanningHaunter(BaseHaunter):
    """
    Incomplete haunter for users with unfinished planning sessions.

    Handles gentle follow-up when users have started but not completed
    their planning sessions. Uses an encouraging, supportive tone
    to help users finish what they started.
    """

    def __init__(
        self,
        session_id: Union[int, UUID],
        slack: AsyncWebClient,
        scheduler: AsyncIOScheduler,
        channel: str,
        thread_ts: Optional[str] = None,
        incomplete_reason: Optional[str] = None,
    ):
        """
        Initialize incomplete haunter.

        Args:
            session_id: Unique identifier for the haunting session
            slack: Slack web client for messaging
            scheduler: APScheduler instance for job scheduling
            channel: Slack channel or user ID to haunt
            thread_ts: Optional thread timestamp for threaded replies
            incomplete_reason: Optional reason why planning was incomplete
        """
        super().__init__(session_id, slack, scheduler)
        self.channel = channel
        self.thread_ts = thread_ts
        self.incomplete_reason = incomplete_reason

        # Incomplete-specific configuration
        self.backoff_base_minutes = 20  # Longer intervals for incomplete sessions
        self.backoff_cap_minutes = 180  # 3 hours max

    async def parse_intent(self, text: str) -> IncompleteAction:
        """
        Parse user text into an IncompleteAction using LLM.

        Overrides base implementation to use IncompleteAction schema
        and incomplete-specific system prompt.

        Args:
            text: User's raw text input

        Returns:
            IncompleteAction with parsed intent
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
                    {"role": "system", "content": INCOMPLETE_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_tokens=200,
            )

            # Parse JSON response into IncompleteAction
            json_content = response.choices[0].message.content
            if json_content:
                json_content = json_content.strip()
                import json

                action_data = json.loads(json_content)
                return IncompleteAction(**action_data)
            else:
                self.logger.error("Empty response from LLM")
                return IncompleteAction(action="unknown")

        except Exception as e:
            self.logger.error(f"Failed to parse incomplete intent from '{text}': {e}")
            return IncompleteAction(action="unknown")

    async def _route_to_planner(self, intent: IncompleteAction) -> bool:
        """
        Route parsed intent to PlanningAgent for calendar operations.

        Args:
            intent: Parsed IncompleteAction from user input

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
                commit_time_str=None,  # IncompleteAction doesn't have commit_time_str
            )

            # Route to planning agent
            router = RouterAgent()
            result = await router.route_payload(payload)

            if result:
                self.logger.info(
                    f"Successfully routed incomplete intent to planner: {intent.action}"
                )
                return True
            else:
                self.logger.error(f"Failed to route incomplete intent: {intent.action}")
                return False

        except Exception as e:
            self.logger.error(f"Failed to route incomplete intent to planner: {e}")
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

            self.logger.info(f"Parsed incomplete intent: {intent.action}")

            # Handle based on action type
            if intent.action == "postpone":
                # Route to planning agent for rescheduling
                success = await self._route_to_planner(intent)

                if success:
                    await self.send(
                        "✅ No worries! I'll help you reschedule time to finish your planning.",
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    return True
                else:
                    # Schedule follow-up with exponential back-off
                    success = self._schedule_followup(attempt, "incomplete_followup")

                    if success:
                        delay = self.next_delay(attempt + 1)
                        await self.send(
                            f"I had trouble with rescheduling. I'll check back in {delay} minutes.",
                            channel=self.channel,
                            thread_ts=self.thread_ts,
                        )
                        return True
                    else:
                        await self.send(
                            "I'll try to help with rescheduling later.",
                            channel=self.channel,
                            thread_ts=self.thread_ts,
                        )
                        return False

            else:  # unknown action
                # Try to clarify with user about their incomplete planning
                await self.send(
                    "I see you started your planning session but didn't finish it. Would you like to:\n"
                    "• **Postpone** and schedule time to complete your planning?\n"
                    "• Tell me what happened so I can help better?\n\n"
                    "No judgment - sometimes planning sessions get interrupted! "
                    "The important thing is getting back to it when you can.",
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to handle incomplete user reply: {e}")
            await self.send(
                "Sorry, I encountered an error. Let me try again later.",
                channel=self.channel,
                thread_ts=self.thread_ts,
            )
            return False

    async def send_incomplete_followup(self) -> Optional[str]:
        """
        Send initial follow-up about incomplete planning session.

        Returns:
            Message timestamp if successful, None otherwise
        """
        if self.incomplete_reason:
            message = (
                f"👋 Hi! I noticed your planning session was incomplete ({self.incomplete_reason}).\n\n"
                "No worries - life happens! Planning sessions sometimes get interrupted. "
                "The important thing is getting back to it when you can.\n\n"
                "Would you like me to help you **postpone** and schedule time to finish your planning? "
                "Even 15-20 minutes can help you wrap up where you left off."
            )
        else:
            message = (
                "👋 Hi! I noticed your planning session was incomplete.\n\n"
                "No worries - life happens! Planning sessions sometimes get interrupted. "
                "The important thing is getting back to it when you can.\n\n"
                "Would you like me to help you **postpone** and schedule time to finish your planning? "
                "Even 15-20 minutes can help you wrap up where you left off."
            )

        return await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

    async def _send_followup_reminder(self, attempt: int) -> None:
        """
        Send incomplete-specific follow-up reminder.

        Args:
            attempt: Current attempt number
        """
        messages = [
            "👻 Still thinking about finishing that planning session?",
            "📋 No pressure, but completing your planning could be really helpful!",
            "🌱 Even a quick 15 minutes to wrap up your planning thoughts could be valuable.",
            "💡 Sometimes the best planning happens when we come back to it fresh!",
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
            f"Sent incomplete follow-up #{attempt} for session {self.session_id}"
        )

    async def send_gentle_encouragement(self) -> Optional[str]:
        """
        Send gentle encouragement about the value of completing planning.

        Returns:
            Message timestamp if successful, None otherwise
        """
        message = (
            "🌟 Just a gentle reminder about the planning session you started.\n\n"
            "Incomplete planning is still valuable! Even if you only had a few minutes, "
            "that thinking time matters. But if you'd like to wrap it up, I'm here to help.\n\n"
            "Research shows that even brief planning sessions can:\n"
            "• Reduce stress and mental clutter\n"
            "• Improve focus on priorities\n"
            "• Help you feel more in control\n\n"
            "No pressure though - just wanted you to know the option is there!"
        )

        return await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )
