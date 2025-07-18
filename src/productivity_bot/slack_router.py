"""
Slack Router - Structured event handling for planning threads.

This module provides the new generation Slack event router that replaces
the legacy slack_event_router.py with proper structured output handling,
database session lookup, and comprehensive error handling.
"""

from datetime import timedelta
from typing import Any, Optional

from slack_bolt.async_app import AsyncApp

from . import models
from .agents.planner_agent import send_to_planner_intent
from .common import get_logger
from .database import PlanningSessionService
from .scheduler import cancel_haunt_by_session, reschedule_haunt

logger = get_logger("slack_router")


class SlackRouter:
    """
    New generation Slack event router with structured output support.

    This router handles Slack events for planning threads using:
    - Structured LLM intent parsing via OpenAI Structured Outputs
    - Database session lookup by channel_id/thread_ts
    - Proper error handling and user feedback
    - Action execution with calendar integration
    """

    def __init__(self, app: AsyncApp):
        """Initialize the Slack router with the app instance."""
        self.app = app
        self._register_handlers()

    def _register_handlers(self):
        """Register Slack event handlers."""

        @self.app.event("message")
        async def handle_thread_messages(event, say, client):
            """Handle messages in planning threads."""
            # Only process thread replies
            if not event.get("thread_ts"):
                return

            thread_ts = event["thread_ts"]
            user_text = event.get("text", "").strip()
            user_id = event.get("user", "")
            channel = event.get("channel", "")

            # Skip empty messages or bot messages
            if not user_text or event.get("bot_id"):
                return

            logger.info(
                f"Processing thread message: '{user_text}' in thread {thread_ts}"
            )

            # Look up planning session by thread timestamp
            session = await self._get_session_by_thread(thread_ts, channel)

            if not session:
                logger.debug(f"No planning session found for thread {thread_ts}")
                return

            # Process the user input with structured intent parsing
            await self._process_planning_thread_reply(
                thread_ts=thread_ts,
                user_text=user_text,
                user_id=user_id,
                channel=channel,
                planning_session=session,
                say=say,
            )

    async def _get_session_by_thread(
        self, thread_ts: str, channel_id: str
    ) -> Optional[Any]:
        """
        Look up planning session by thread timestamp and channel.

        This method checks if the thread corresponds to a planning session
        by looking up sessions that might have been created with this thread_ts.

        Args:
            thread_ts: Slack thread timestamp
            channel_id: Slack channel ID

        Returns:
            PlanningSession if found, None otherwise
        """
        try:
            # Use the database service to look up session by thread
            service = PlanningSessionService()
            session = await service.get_session_by_thread(thread_ts, channel_id)
            return session

        except Exception as e:
            logger.error(f"Error looking up session by thread {thread_ts}: {e}")
            return None

    async def _process_planning_thread_reply(
        self,
        thread_ts: str,
        user_text: str,
        user_id: str,
        channel: str,
        planning_session: Any,
        say,
    ) -> None:
        """
        Process a user reply in a planning thread using structured LLM parsing.

        Args:
            thread_ts: The Slack thread timestamp
            user_text: User's message text
            user_id: Slack user ID
            channel: Slack channel ID
            planning_session: The associated planning session
            say: Slack say function for responses
        """
        try:
            # 1. Use structured LLM parsing instead of regex
            logger.info(f"Processing structured intent from: '{user_text}'")
            intent = await send_to_planner_intent(user_text)

            # 2. Execute the structured action
            await self._execute_structured_action(
                intent=intent,
                planning_session=planning_session,
                thread_ts=thread_ts,
                user_id=user_id,
                say=say,
            )

        except Exception as e:
            logger.error(f"Error processing planning thread reply: {e}")
            await say(
                text="❌ Sorry, I couldn't understand your request. Try one of these:\n"
                "• `postpone X minutes` - delay the session\n"
                "• `done` - mark the session complete\n"
                "• `recreate event` - recreate the calendar event",
                thread_ts=thread_ts,
            )

    async def _execute_structured_action(
        self,
        intent,
        planning_session: Any,
        thread_ts: str,
        user_id: str,
        say,
    ) -> None:
        """
        Execute a structured action based on LLM intent parsing.

        Args:
            intent: PlannerAction object from structured output
            planning_session: The planning session to act on
            thread_ts: Thread timestamp for responses
            user_id: User ID for notifications
            say: Slack say function
        """
        try:
            action = intent.action

            if action == "postpone":
                await self._handle_postpone_action(
                    planning_session=planning_session,
                    minutes=intent.minutes or 15,  # Default to 15 minutes
                    thread_ts=thread_ts,
                    say=say,
                )

            elif action == "mark_done":
                await self._handle_mark_done_action(
                    planning_session=planning_session,
                    thread_ts=thread_ts,
                    say=say,
                )

            elif action == "create_event":
                await self._handle_create_event_action(
                    planning_session=planning_session,
                    thread_ts=thread_ts,
                    say=say,
                )

            else:
                logger.warning(f"Unknown action type: {action}")
                await say(
                    text=f"❓ I understood your intent but don't know how to handle '{action}' yet.",
                    thread_ts=thread_ts,
                )

        except Exception as e:
            logger.error(f"Error executing structured action {intent.action}: {e}")
            await say(
                text="❌ Sorry, there was an error processing your request. Please try again.",
                thread_ts=thread_ts,
            )

    async def _handle_postpone_action(
        self,
        planning_session: Any,
        minutes: int,
        thread_ts: str,
        say,
    ) -> None:
        """Handle postpone action with scheduler integration."""
        try:
            from datetime import datetime

            # Calculate new time
            new_time = planning_session.scheduled_for + timedelta(minutes=minutes)

            # Try to reschedule the haunt job
            if planning_session.scheduler_job_id:
                success = reschedule_haunt(planning_session.id, new_time)
                if success:
                    # Update the session's scheduled time
                    planning_session.scheduled_for = new_time
                    service = PlanningSessionService()
                    await service.update_session(planning_session)

                    await say(
                        text=f"⏰ Planning session postponed by {minutes} minutes.\n"
                        f"New scheduled time: {new_time.strftime('%I:%M %p')}",
                        thread_ts=thread_ts,
                    )
                    logger.info(
                        f"Postponed session {planning_session.id} by {minutes} minutes"
                    )
                else:
                    await say(
                        text=f"⚠️ Acknowledged postpone request for {minutes} minutes, but couldn't update scheduler.",
                        thread_ts=thread_ts,
                    )
            else:
                # No scheduler job, just acknowledge
                await say(
                    text=f"⏰ Planning session postponed by {minutes} minutes.\n"
                    f"New scheduled time: {new_time.strftime('%I:%M %p')}",
                    thread_ts=thread_ts,
                )

        except Exception as e:
            logger.error(f"Error postponing session: {e}")
            await say(
                text="❌ Failed to postpone the session. Please try again.",
                thread_ts=thread_ts,
            )

    async def _handle_mark_done_action(
        self,
        planning_session: Any,
        thread_ts: str,
        say,
    ) -> None:
        """Handle mark done action with database and scheduler integration."""
        try:
            # Cancel any scheduled haunt jobs
            if planning_session.scheduler_job_id:
                cancel_success = cancel_haunt_by_session(planning_session.id)
                if cancel_success:
                    logger.info(
                        f"Cancelled haunt job for session {planning_session.id}"
                    )

            # Update session status in database
            service = PlanningSessionService()
            planning_session.mark_complete()
            await service.update_session(planning_session)

            await say(
                text="✅ Planning session marked as complete! Great job staying on track.",
                thread_ts=thread_ts,
            )

            logger.info(f"Marked session {planning_session.id} as complete")

        except Exception as e:
            logger.error(f"Error marking session complete: {e}")
            await say(
                text="❌ Failed to mark session complete. Please try again.",
                thread_ts=thread_ts,
            )

    async def _handle_create_event_action(
        self,
        planning_session: Any,
        thread_ts: str,
        say,
    ) -> None:
        """Handle recreate event action."""
        try:
            # Recreate the calendar event
            success = await planning_session.create_event()

            if success:
                await say(
                    text="📅 Calendar event recreated successfully!",
                    thread_ts=thread_ts,
                )
                logger.info(
                    f"Recreated calendar event for session {planning_session.id}"
                )
            else:
                await say(
                    text="❌ Failed to recreate calendar event. Please check your calendar integration.",
                    thread_ts=thread_ts,
                )

        except Exception as e:
            logger.error(f"Error recreating calendar event: {e}")
            await say(
                text="❌ Failed to recreate calendar event. Please try again.",
                thread_ts=thread_ts,
            )
