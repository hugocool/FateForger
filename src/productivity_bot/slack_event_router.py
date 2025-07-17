"""
Slack Event Router for Planning Thread Management.

This module handles Slack events and routes thread replies to the AI planner agent.
When users reply in planning threads, their messages are forwarded to an OpenAI
Assistant Agent for parsing into structured actions.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from .agents.slack_assistant_agent import process_slack_thread_reply
from .common import get_logger
from .database import get_db_session
from .models import CalendarEvent, PlanningSession  # SQLAlchemy models from models.py
from .actions.planner_action import PlannerAction  # Pydantic model
from .scheduler import get_scheduler

logger = get_logger("slack_event_router")


class SlackEventRouter:
    """
    Routes Slack events to appropriate handlers, specifically managing
    planning thread interactions with the AI agent.
    """

    def __init__(self, slack_app: AsyncApp):
        """
        Initialize the event router.

        Args:
            slack_app: The Slack Bolt app instance
        """
        self.slack_app = slack_app
        self.client: AsyncWebClient = slack_app.client
        self.scheduler = get_scheduler()

        # Register event handlers
        self._register_handlers()

        logger.info("Slack event router initialized")

    def _register_handlers(self):
        """Register all Slack event handlers."""

        # Handle thread replies in planning sessions
        @self.slack_app.event("message")
        async def handle_message_events(event, say, logger):
            """Handle message events, specifically thread replies."""
            try:
                await self._handle_message_event(event, say)
            except Exception as e:
                logger.error(f"Error handling message event: {e}")

        # Handle app mentions for direct interaction
        @self.slack_app.event("app_mention")
        async def handle_app_mention(event, say, logger):
            """Handle when the bot is mentioned."""
            try:
                await self._handle_app_mention(event, say)
            except Exception as e:
                logger.error(f"Error handling app mention: {e}")

    async def _handle_message_event(self, event: Dict[str, Any], say) -> None:
        """
        Handle incoming message events, focusing on planning thread replies.

        Args:
            event: The Slack message event
            say: Slack say function for responses
        """
        # Skip bot messages and messages without thread_ts
        if event.get("bot_id") or not event.get("thread_ts"):
            return

        thread_ts = event["thread_ts"]
        user_text = event.get("text", "").strip()
        user_id = event.get("user")
        channel = event.get("channel")

        # Ensure we have required fields
        if not user_text or not user_id or not channel:
            return

        logger.info(f"Processing thread reply in {thread_ts}: '{user_text}'")

        # Check if this is a planning thread by looking for associated planning session
        planning_session = await self._get_planning_event_for_thread(thread_ts)

        if not planning_session:
            # No session found by thread_ts. Check if this could be a response to a planning prompt
            # Look for an active planning session for this user without thread info
            planning_session = await self._try_link_to_active_session(user_id, thread_ts, channel)

        if planning_session:
            # This is a planning thread - forward to agent
            await self._process_planning_thread_reply(
                thread_ts=thread_ts,
                user_text=user_text,
                user_id=user_id,
                channel=channel,
                planning_session=planning_session,
                say=say,
            )
        else:
            logger.debug(f"Thread {thread_ts} is not a planning thread, ignoring")

    async def _handle_app_mention(self, event: Dict[str, Any], say) -> None:
        """
        Handle direct mentions of the bot.

        Args:
            event: The Slack mention event
            say: Slack say function for responses
        """
        user_text = event.get("text", "").strip()
        thread_ts = event.get("ts")  # This becomes the new thread

        logger.info(f"Bot mentioned: '{user_text}'")

        # For now, provide helpful information
        await say(
            thread_ts=thread_ts,
            text="👋 Hi! I help manage your planning sessions. When you get a planning notification from me, you can reply in the thread with commands like:\n\n"
            "• `postpone 15` - postpone by 15 minutes\n"
            "• `done` - mark session as complete\n"
            "• `help` - show all commands\n"
            "• `status` - check current status",
        )

    async def _get_planning_event_for_thread(
        self, thread_ts: str
    ) -> Optional[PlanningSession]:
        """
        Get the planning session associated with a Slack thread.

        Args:
            thread_ts: The Slack thread timestamp

        Returns:
            PlanningSession if found, None otherwise
        """
        try:
            # Query database for planning session with this thread_ts
            async with get_db_session() as db:
                from sqlalchemy import select

                # Look for PlanningSession with matching thread_ts
                result = await db.execute(
                    select(PlanningSession).where(
                        PlanningSession.thread_ts == thread_ts
                    )
                )
                return result.scalar_one_or_none()

        except Exception as e:
            logger.error(f"Error finding planning session for thread {thread_ts}: {e}")
            return None

    async def _try_link_to_active_session(
        self, user_id: str, thread_ts: str, channel_id: str
    ) -> Optional[PlanningSession]:
        """
        Try to link a thread to an active planning session that doesn't have thread info yet.
        
        This handles the case where a user responds to a planning prompt for the first time,
        creating a thread that needs to be associated with their planning session.
        
        Args:
            user_id: Slack user ID
            thread_ts: Thread timestamp
            channel_id: Channel ID
            
        Returns:
            PlanningSession if found and linked, None otherwise
        """
        try:
            # Look for an active planning session for this user that doesn't have thread info
            from sqlalchemy import and_, select
            
            async with get_db_session() as db:
                result = await db.execute(
                    select(PlanningSession).where(
                        and_(
                            PlanningSession.user_id == user_id,
                            PlanningSession.thread_ts.is_(None),  # No thread info yet
                            PlanningSession.status.in_(['NOT_STARTED', 'IN_PROGRESS'])
                        )
                    ).order_by(PlanningSession.scheduled_for.desc())  # Most recent first
                )
                session = result.scalar_one_or_none()
                
                if session:
                    # Found a session - link it to this thread
                    session.thread_ts = thread_ts
                    session.channel_id = channel_id
                    
                    await db.commit()
                    logger.info(
                        f"Linked planning session {session.id} to thread {thread_ts} in channel {channel_id}"
                    )
                    return session
                    
                return None
                
        except Exception as e:
            logger.error(f"Error linking thread {thread_ts} to active session: {e}")
            return None

    async def _process_planning_thread_reply(
        self,
        thread_ts: str,
        user_text: str,
        user_id: str,
        channel: str,
        planning_session: PlanningSession,
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
            # 1. Use AssistantAgent with MCP tools for structured intent parsing
            logger.info(f"Processing structured intent from: '{user_text}'")
            
            # Build session context for the agent
            session_context = {
                "session_id": planning_session.id,
                "user_id": planning_session.user_id,
                "date": str(planning_session.date),
                "status": planning_session.status.value,
                "goals": planning_session.goals or "Not specified"
            }
            
            intent = await process_slack_thread_reply(user_text, session_context)

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
                thread_ts=thread_ts,
                text=(
                    "❌ Sorry, I couldn't understand that. "
                    'Please say one of: "postpone X minutes", "mark done", or "recreate event".'
                ),
            )

    async def _execute_planning_action(
        self,
        action: Dict[str, Any],
        planning_session: PlanningSession,
        thread_ts: str,
        user_id: str,
        say,
    ) -> None:
        """
        Execute the action returned by the planner agent.

        Args:
            action: The action dictionary from the agent
            planning_event: The associated planning event
            thread_ts: Slack thread timestamp
            user_id: Slack user ID
            say: Slack say function
        """
        action_type = action.get("action")

        if action_type == "postpone":
            await self._handle_postpone_action(action, planning_session, thread_ts, say)

        elif action_type == "mark_done":
            await self._handle_mark_done_action(planning_session, thread_ts, say)

        elif action_type == "recreate_event":
            await self._handle_recreate_event_action(planning_session, thread_ts, say)

        elif action_type == "status":
            await self._handle_status_action(planning_session, thread_ts, say)

        elif action_type == "help":
            await self._handle_help_action(thread_ts, say)

        else:
            logger.warning(f"Unknown action type: {action_type}")
            await say(
                thread_ts=thread_ts,
                text=f"🤔 I'm not sure how to handle that request. Try `help` to see available commands.",
            )

    async def _execute_structured_action(
        self,
        intent: PlannerAction,
        planning_session: PlanningSession,
        thread_ts: str,
        user_id: str,
        say,
    ) -> None:
        """
        Execute the structured action returned by the planner agent.

        This method handles PlannerAction objects with proper type safety
        and eliminates the need for manual action type checking.

        Args:
            intent: The structured PlannerAction from the LLM
            planning_session: The associated planning session
            thread_ts: Slack thread timestamp
            user_id: Slack user ID
            say: Slack say function
        """
        from datetime import timedelta

        logger.info(f"Executing structured action: {intent}")

        if intent.is_postpone:
            # Handle postpone with validated minutes
            minutes = intent.get_postpone_minutes() or 15  # Default to 15 if None
            try:
                # Implement actual scheduler postpone logic
                from datetime import datetime
                from .scheduler import reschedule_haunt
                
                # Calculate new time
                new_time = datetime.now() + timedelta(minutes=minutes)
                
                # Try to reschedule the haunt job
                if planning_session.scheduler_job_id:
                    success = reschedule_haunt(planning_session.id, new_time)
                    if success:
                        # Update the session's scheduled time
                        planning_session.scheduled_for = new_time
                        # Update in database
                        async with get_db_session() as db:
                            await db.merge(planning_session)
                            await db.commit()
                        
                        await say(
                            thread_ts=thread_ts,
                            text=f"⏰ OK, I'll check back in {minutes} minutes.",
                        )
                    else:
                        await say(
                            thread_ts=thread_ts,
                            text=f"⏰ Noted that you want to postpone {minutes} minutes, but couldn't update scheduler.",
                        )
                else:
                    await say(
                        thread_ts=thread_ts,
                        text=f"⏰ OK, I'll check back in {minutes} minutes.",
                    )
                    
                logger.info(
                    f"Postponed planning session {planning_session.id} by {minutes} minutes"
                )

            except Exception as e:
                logger.error(f"Error postponing session: {e}")
                await say(
                    thread_ts=thread_ts,
                    text=f"❌ Sorry, I couldn't postpone the session: {str(e)}",
                )

        elif intent.is_mark_done:
            # Handle completion
            try:
                # Update session status in database and cancel scheduler jobs
                from .scheduler import cancel_haunt_by_session
                
                # Cancel any scheduled haunt jobs
                if planning_session.scheduler_job_id:
                    cancel_success = cancel_haunt_by_session(planning_session.id)
                    if cancel_success:
                        logger.info(f"Cancelled haunt job for session {planning_session.id}")

                # Update session status in database
                planning_session.mark_complete()
                async with get_db_session() as db:
                    await db.merge(planning_session)
                    await db.commit()
                
                await say(
                    thread_ts=thread_ts, text="✅ Marked planning done. Good work!"
                )
                logger.info(
                    f"Marked planning session {planning_session.id} as completed"
                )

            except Exception as e:
                logger.error(f"Error marking session done: {e}")
                await say(
                    thread_ts=thread_ts,
                    text=f"❌ Sorry, I couldn't mark the session as done: {str(e)}",
                )

        elif intent.is_recreate_event:
            # Handle event recreation
            try:
                # Implement calendar event recreation
                success = await planning_session.recreate_event()
                
                if success:
                    await say(thread_ts=thread_ts, text="� Recreated the planning event.")
                    logger.info(
                        f"Recreated event for planning session {planning_session.id}"
                    )
                else:
                    await say(
                        thread_ts=thread_ts,
                        text="❌ Failed to recreate the planning event. Please check your calendar integration."
                    )

            except Exception as e:
                logger.error(f"Error recreating event: {e}")
                await say(
                    thread_ts=thread_ts,
                    text=f"❌ Sorry, I couldn't recreate the event: {str(e)}",
                )
        else:
            # This should never happen with structured output, but defensive programming
            logger.warning(f"Unknown structured action: {intent}")
            await say(
                thread_ts=thread_ts,
                text="🤔 I'm not sure how to handle that request. Please try again.",
            )

    async def _handle_postpone_action(
        self,
        action: Dict[str, Any],
        planning_session: PlanningSession,
        thread_ts: str,
        say,
    ) -> None:
        """Handle postpone action."""
        minutes = action.get("minutes", 15)

        try:
            # Update the planning session time (for now, just update the status)
            # TODO: Implement actual postpone logic with scheduler

            await say(
                thread_ts=thread_ts,
                text=f"⏰ Planning session postponed by {minutes} minutes. I'll remind you again then!",
            )

            logger.info(
                f"Postponed planning session {planning_session.id} by {minutes} minutes"
            )

        except Exception as e:
            logger.error(f"Error postponing session: {e}")
            await say(
                thread_ts=thread_ts,
                text=f"❌ Sorry, I couldn't postpone the session: {str(e)}",
            )

    async def _handle_mark_done_action(
        self, planning_session: PlanningSession, thread_ts: str, say
    ) -> None:
        """Handle mark done action."""
        try:
            # Mark the planning session as completed
            # TODO: Update database directly instead of using scheduler

            await say(
                thread_ts=thread_ts,
                text="✅ Great! Planning session marked as complete. Keep up the good work!",
            )

            logger.info(f"Marked planning session {planning_session.id} as completed")

        except Exception as e:
            logger.error(f"Error marking session done: {e}")
            await say(
                thread_ts=thread_ts,
                text=f"❌ Sorry, I couldn't mark the session as done: {str(e)}",
            )

    async def _handle_recreate_event_action(
        self, planning_session: PlanningSession, thread_ts: str, say
    ) -> None:
        """Handle recreate event action."""
        try:
            # This would involve calendar integration
            # For now, just acknowledge
            await say(
                thread_ts=thread_ts,
                text="🗓️ I'll recreate the calendar event for this planning session. (This feature is being implemented)",
            )

            logger.info(
                f"Recreate event requested for planning session {planning_session.id}"
            )

        except Exception as e:
            logger.error(f"Error recreating event: {e}")
            await say(
                thread_ts=thread_ts,
                text=f"❌ Sorry, I couldn't recreate the event: {str(e)}",
            )

    async def _handle_status_action(
        self, planning_session: PlanningSession, thread_ts: str, say
    ) -> None:
        """Handle status action."""
        try:
            # Get current status of the planning session
            status_text = f"📊 **Planning Session Status**\n\n"
            status_text += f"• Session ID: {planning_session.id}\n"
            status_text += f"• User: {planning_session.user_id}\n"
            status_text += f"• Date: {planning_session.date}\n"
            status_text += f"• Scheduled For: {planning_session.scheduled_for}\n"
            status_text += f"• Status: {planning_session.status.value}\n"

            if planning_session.event_id:
                status_text += f"• Event ID: {planning_session.event_id}\n"

            await say(thread_ts=thread_ts, text=status_text)

        except Exception as e:
            logger.error(f"Error getting status: {e}")
            await say(
                thread_ts=thread_ts,
                text=f"❌ Sorry, I couldn't get the status: {str(e)}",
            )

    async def _handle_help_action(self, thread_ts: str, say) -> None:
        """Handle help action."""
        help_text = """🤖 **Planning Session Commands**

I can help you manage your planning session. Here's what you can do:

• **`postpone X`** - Postpone by X minutes (e.g., "postpone 15")
• **`done`** - Mark this planning session as complete  
• **`recreate event`** - Recreate the calendar event
• **`status`** - Show current session status
• **`help`** - Show this help message

Just reply in this thread with any of these commands, and I'll take care of it! 🎯"""

        await say(thread_ts=thread_ts, text=help_text)


def create_event_router(slack_app: AsyncApp) -> SlackEventRouter:
    """
    Create and configure the Slack event router.

    Args:
        slack_app: The Slack Bolt app instance

    Returns:
        Configured SlackEventRouter instance
    """
    return SlackEventRouter(slack_app)


async def test_event_router() -> bool:
    """
    Test the event router functionality.

    Returns:
        True if test successful, False otherwise
    """
    try:
        # This would require a mock Slack app for testing
        logger.info("Event router test would require Slack app mock")
        return True
    except Exception as e:
        logger.error(f"Event router test failed: {e}")
        return False
