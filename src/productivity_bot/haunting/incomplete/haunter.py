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
                clarification_message = await self.generate_message("incomplete_clarification", 1)
                await self.send(
                    clarification_message,
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
                return False

        except Exception as e:
            self.logger.error(f"Failed to handle incomplete user reply: {e}")
            error_message = await self.generate_message("error_response", 1)
            await self.send(
                error_message,
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
        # Generate LLM-powered incomplete followup message
        message = await self.generate_message("incomplete_followup_initial", 1)

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
        # Generate LLM-powered message instead of cycling through hardcoded ones
        message = await self.generate_message("incomplete_followup", attempt)

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
        # Generate LLM-powered encouragement message
        message = await self.generate_message("gentle_encouragement", 1)

        return await self.send(
            message,
            channel=self.channel,
            thread_ts=self.thread_ts,
        )

    # ========================================================================
    # Overdue Session Polling
    # ========================================================================

    @classmethod
    async def poll_overdue_sessions(cls):
        """Poll for overdue sessions and start incomplete haunting."""
        try:
            from datetime import datetime, timedelta
            from sqlalchemy import and_, select
            
            from ...database import get_db_session
            from ...models import PlanningSession, PlanStatus, BaseEvent
            
            logger = logging.getLogger(__name__)
            
            # Find sessions that are overdue (event ended but session not complete)
            cutoff_time = datetime.now() - timedelta(hours=2)  # 2 hours grace period
            
            async with get_db_session() as db:
                result = await db.execute(
                    select(PlanningSession, BaseEvent)
                    .join(BaseEvent, PlanningSession.event_id == BaseEvent.event_id)
                    .where(
                        and_(
                            BaseEvent.end_time < cutoff_time,
                            PlanningSession.status.in_([PlanStatus.NOT_STARTED, PlanStatus.IN_PROGRESS]),
                        )
                    )
                )
                overdue_sessions = result.all()
                
                logger.info(f"Found {len(overdue_sessions)} overdue sessions")
                
                # TODO: Start incomplete haunters for overdue sessions
                # This would require scheduler and Slack client setup
                # For now, just log the sessions found
                for session, event in overdue_sessions:
                    logger.info(f"Overdue session: {session.id} for user {session.user_id}")
                    
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to poll overdue sessions: {e}")

    async def start_incomplete_haunt(self):
        """Start haunting for an incomplete/overdue session."""
        try:
            # Generate and send initial incomplete message
            incomplete_message = await self.generate_message("incomplete_start", 1)
            message_ts = await self.send(
                incomplete_message,
                channel=self.channel,
                thread_ts=self.thread_ts,
            )
            
            if message_ts:
                self.logger.info(f"Sent incomplete start message for session {self.session_id}: {message_ts}")
                # Schedule escalating follow-ups for incomplete sessions
                from datetime import datetime, timedelta
                
                # First follow-up in 4 hours
                followup_time = datetime.now() + timedelta(hours=4)
                followup_message = await self.generate_message("incomplete_followup", 2)
                
                await self.schedule_slack(
                    text=followup_message,
                    post_at=followup_time,
                    channel=self.channel,
                    thread_ts=self.thread_ts,
                )
                
            else:
                self.logger.error(f"Failed to send incomplete start message for session {self.session_id}")
                
        except Exception as e:
            self.logger.error(f"Failed to start incomplete haunt for session {self.session_id}: {e}")

    def _get_message_system_prompt(self, context: str, attempt: int) -> str:
        """
        Get incomplete-specific system prompt for message generation.
        
        Args:
            context: Message context 
            attempt: Current attempt number
            
        Returns:
            Incomplete-specific system prompt
        """
        if context == "incomplete_start":
            return """You are a caring productivity assistant reaching out about a missed planning session.

Generate a supportive, non-judgmental message that:
- Acknowledges they missed their planned session time
- Emphasizes that it's okay and life happens
- Offers to help them reschedule or complete the planning now
- Maintains encouraging tone without guilt or pressure
- Uses warm, understanding emojis (💙, 🤗, 📅, ✨, etc.)

Keep it supportive and solution-focused rather than dwelling on what was missed."""

        elif context == "incomplete_followup":
            if attempt <= 3:
                tone = "gentle and patient"
            elif attempt <= 5:
                tone = "more direct but understanding"
            else:
                tone = "persistent but caring"
                
            return f"""You are a caring productivity assistant following up on a missed planning session (attempt #{attempt}).

Generate a {tone} follow-up message that:
- Shows continued support despite the missed session
- Offers flexible options for completing the planning
- Acknowledges that sometimes planning gets delayed
- Provides encouragement to get back on track
- Uses appropriate emojis for attempt #{attempt}

Keep it understanding while gently encouraging action."""

        elif context == "incomplete_followup_initial":
            return """You are a caring productivity assistant reaching out about an incomplete planning session.

Generate a supportive initial follow-up message that:
- Acknowledges their planning session was incomplete
- Shows understanding that interruptions happen
- Offers to help them reschedule or finish the planning
- Emphasizes the value of completing even brief planning
- Uses warm, encouraging emojis (👋, 💙, 📅, etc.)

Keep it non-judgmental and focused on helping them get back on track."""

        elif context == "gentle_encouragement":
            return """You are an encouraging productivity assistant providing gentle motivation about incomplete planning.

Generate a supportive encouragement message that:
- Validates that incomplete planning still has value
- Shares benefits of even brief planning sessions
- Offers to help them finish if they'd like
- Maintains no-pressure tone
- Uses positive, encouraging emojis (🌟, 💡, 🌱, etc.)

Keep it validating and informative without being pushy."""

        elif context == "incomplete_clarification":
            return """You are a helpful productivity assistant asking for clarification about an incomplete planning session.

Generate a clarifying message that:
- Acknowledges their incomplete planning session
- Offers specific options for next steps (postpone, etc.)
- Shows understanding that sessions get interrupted
- Maintains supportive, non-judgmental tone
- Uses friendly emojis (❓, 💭, 🤝, etc.)

Keep it clear and helpful while showing understanding."""

        elif context == "error_response":
            return """You are a apologetic productivity assistant handling a technical error.

Generate a brief error message that:
- Apologizes for the technical issue
- Assures them you'll try again later
- Maintains professional tone
- Uses appropriate emojis (😅, 🔧, etc.)

Keep it brief and professional."""

        else:
            # Fallback to base implementation
            return super()._get_message_system_prompt(context, attempt)
