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
                await self.send(
                    "🎉 Awesome! Great job completing your planning session. "
                    "I'll check in with you about your next one later.",
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
                    await self.send(
                        "✅ No problem! I'll help you reschedule your planning session.",
                        channel=self.channel,
                        thread_ts=self.thread_ts,
                    )
                    return True
                else:
                    # Schedule follow-up with exponential back-off
                    success = self._schedule_followup(attempt, "commitment_followup")

                    if success:
                        delay = self.next_delay(attempt + 1)
                        await self.send(
                            f"I had trouble rescheduling. I'll check back in {delay} minutes.",
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
                # Try to clarify with user
                await self.send(
                    "I'm not sure what you mean. Since you committed to a planning session, I'd like to know:\n"
                    "• Did you **complete** your planning session?\n"
                    "• Do you need to **postpone** it to another time?\n"
                    "\nJust let me know how it went!",
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to handle commitment user reply: {e}")
            await self.send(
                "Sorry, I encountered an error. Let me try again later.",
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
        if self.planned_time:
            time_str = self.planned_time.strftime("%I:%M %p on %B %d")
            message = (
                f"⏰ Hey! Just checking in about your planning session scheduled for {time_str}.\n\n"
                "How did it go? Did you:\n"
                "• **Complete** your planning session as scheduled?\n"
                "• Need to **postpone** to a different time?\n\n"
                "Let me know so I can help you stay on track!"
            )
        else:
            message = (
                "⏰ Hey! Just checking in about your planning session.\n\n"
                "How did it go? Did you:\n"
                "• **Complete** your planning session?\n"
                "• Need to **postpone** to a different time?\n\n"
                "Let me know so I can help you stay on track!"
            )

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
        messages = [
            "👻 Still wondering about your planning session - how did it go?",
            "📅 Just want to make sure you're staying on track with your planning!",
            "⚡ Quick check-in: did you get that planning time in?",
            "🎯 Accountability check! How was your planning session?",
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

        time_str = self.planned_time.strftime("%I:%M %p")
        message = (
            f"🔔 Heads up! Your planning session is coming up at {time_str} "
            f"(in {minutes_before} minutes).\n\n"
            "This is your dedicated time to:\n"
            "• Review your goals and priorities\n"
            "• Plan your upcoming work\n"
            "• Reflect on your progress\n\n"
            "Hope it goes well! I'll check in afterward to see how it went."
        )

        return await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )
