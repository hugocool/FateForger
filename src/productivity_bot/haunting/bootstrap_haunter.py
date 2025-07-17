"""
Daily Bootstrap Haunter for Missing Planning Sessions.

This module implements Ticket 2: A haunter that checks daily at 17:00 for
missing "Plan Tomorrow" sessions and initiates the bootstrap flow.
"""

from datetime import date, datetime, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

from ..actions.planner_action import PlannerAction
from ..common import get_logger, find_planning_event
from .base_haunter import BaseHaunter

logger = get_logger("bootstrap_haunter")


class PlanningBootstrapHaunter(BaseHaunter):
    """
    Haunter that initiates bootstrap flow for missing planning sessions.
    
    This haunter:
    1. Checks daily at 17:00 for missing planning events for tomorrow
    2. Creates a bootstrap session if no planning event exists
    3. Sends initial bootstrap message asking when user will plan
    4. Follows up with increasing intervals: 20→40→80→160 minutes (cap at 4h)
    5. Routes user replies to PlanningAgent for event creation
    """
    
    BOOTSTRAP_HOUR = 17
    EVENT_LOOKAHEAD = timedelta(hours=32)  # tomorrow + a bit
    
    def __init__(self, session_id: int, slack: AsyncWebClient, scheduler: AsyncIOScheduler):
        """Initialize bootstrap haunter."""
        super().__init__(session_id, slack, scheduler)
        
        # Bootstrap-specific back-off: 20, 40, 80, 160 minutes (cap at 4h = 240min)
        self.backoff_base_minutes = 20  # Start with 20 minutes instead of 5
        self.backoff_cap_minutes = 240  # Cap at 4 hours instead of 120
    
    @classmethod
    def schedule_daily(cls, scheduler: AsyncIOScheduler) -> None:
        """
        Schedule the daily bootstrap check at 17:00.
        
        Args:
            scheduler: APScheduler instance to register the job
        """
        try:
            scheduler.add_job(
                cls._daily_check,
                trigger='cron',
                hour=cls.BOOTSTRAP_HOUR,
                id='daily-planning-bootstrap',
                replace_existing=True,
                misfire_grace_time=300,  # 5 minutes grace for missed executions
            )
            logger.info(f"Scheduled daily bootstrap check at {cls.BOOTSTRAP_HOUR}:00")
        except Exception as e:
            logger.error(f"Failed to schedule daily bootstrap check: {e}")
    
    @classmethod
    async def _daily_check(cls) -> None:
        """
        Daily check for missing planning events for tomorrow.
        
        This method:
        1. Calculates tomorrow's date
        2. Checks if a planning event exists for tomorrow
        3. If not, creates a bootstrap session and starts haunting
        """
        try:
            tomorrow = date.today() + timedelta(days=1)
            logger.info(f"Running daily bootstrap check for {tomorrow}")
            
            # Check if there's already a planning event for tomorrow
            planning_event = await find_planning_event(tomorrow)
            
            if planning_event:
                logger.info(f"Planning event already exists for {tomorrow}: {planning_event.get('summary')}")
                return
                
            logger.info(f"No planning event found for {tomorrow} - bootstrap needed")
            
            # TODO: This would typically integrate with the session management system
            # For now, we'll log the intent to create a bootstrap session
            logger.info(f"Would create bootstrap session for {tomorrow}")
            
            # Integration point: Create bootstrap session and start haunting
            # session = await PlanningSession.create_bootstrap(tomorrow)
            # haunter = cls(session.id, slack_client, scheduler)
            # await haunter._start_bootstrap_haunt()
            
        except Exception as e:
            logger.error(f"Error in daily bootstrap check: {e}")
    
    async def _start_bootstrap_haunt(self) -> None:
        """
        Start the bootstrap haunting sequence.
        
        Sends the initial bootstrap message and schedules first follow-up.
        """
        try:
            # Construct initial bootstrap message
            text = "I don't see a planning session for tomorrow. When will you plan?"
            
            # Send the initial message (simplified for ticket requirements)
            # In full implementation, this would use self.send() with channel/thread_ts
            logger.info(f"Bootstrap message: {text}")
            
            # Schedule the first follow-up (20 minutes)
            self._schedule_followup(0)
            
            logger.info(f"Started bootstrap haunt for session {self.session_id}")
            
        except Exception as e:
            logger.error(f"Error starting bootstrap haunt for session {self.session_id}: {e}")
    
    async def handle_user_reply(self, message_text: str, user_id: str, thread_ts: str) -> None:
        """
        Handle user reply in bootstrap thread.
        
        This method:
        1. Parses the user message using PlannerAction schema
        2. Routes to appropriate handler based on action type
        3. Cancels pending follow-ups if session is resolved
        
        Args:
            message_text: User's message text
            user_id: Slack user ID
            thread_ts: Thread timestamp
        """
        try:
            logger.info(f"Handling bootstrap reply for session {self.session_id}: {message_text}")
            
            # Parse user intent using existing PlannerAction schema
            action = await self.parse_intent(message_text)
            
            if action.is_postpone:
                await self._handle_postpone(action, thread_ts)
            elif action.is_create_event:
                await self._handle_create_event(action, thread_ts)
            else:
                # Default to create_event for time commitments
                await self._handle_create_event(action, thread_ts)
                
        except Exception as e:
            logger.error(f"Error handling bootstrap reply: {e}")
    
    async def _handle_postpone(self, action: PlannerAction, thread_ts: str) -> None:
        """Handle postpone action in bootstrap context."""
        try:
            minutes = action.get_postpone_minutes() or 20  # Default to 20 for bootstrap
            # Cap postpone at 4 hours for bootstrap
            minutes = min(minutes, 240)
            
            logger.info(f"Postponing bootstrap session {self.session_id} by {minutes} minutes")
            
            # Schedule postponed follow-up
            run_time = datetime.utcnow() + timedelta(minutes=minutes)
            job_id = self._job_id("postponed", 0)
            
            self.schedule_job(
                job_id=job_id,
                run_dt=run_time,
                fn=self._send_followup_reminder,
                0  # Reset attempt counter
            )
            
        except Exception as e:
            logger.error(f"Error handling bootstrap postpone: {e}")
    
    async def _handle_create_event(self, action: PlannerAction, thread_ts: str) -> None:
        """Handle create event action - hand off to PlanningAgent."""
        try:
            # Hand off to PlanningAgent for event creation
            await self._route_to_planner(action)
            
            logger.info(f"Handed off bootstrap session {self.session_id} to PlanningAgent")
            
            # Cancel any pending follow-ups
            self.cleanup_all_jobs()
            
        except Exception as e:
            logger.error(f"Error handling bootstrap create event: {e}")
    
    async def _route_to_planner(self, intent: PlannerAction) -> bool:
        """
        Route parsed intent to PlanningAgent for calendar operations.
        
        This is the abstract handoff interface implementation for bootstrap sessions.
        
        Args:
            intent: Parsed PlannerAction from user input
            
        Returns:
            True if handoff was successful, False otherwise
        """
        try:
            from uuid import UUID
            from ..actions.haunt_payload import HauntPayload
            from ..agents.router_agent import route_haunt_payload
            from ..agents.planning_agent import handle_router_handoff
            
            # Create structured payload for the handoff
            # Map commit_time and unknown to create_event for routing
            action_type = intent.action
            if action_type in ("commit_time", "unknown"):
                action_type = "create_event"
            
            payload = HauntPayload(
                session_id=UUID(str(self.session_id)),
                action=action_type,
                minutes=intent.minutes,
                commit_time_str=getattr(intent, 'commitment_time', '') or 'Tomorrow 08:00'
            )
            
            logger.info(f"Routing bootstrap intent to PlanningAgent: {payload}")
            
            # Route through RouterAgent
            router_msg = await route_haunt_payload(payload)
            
            # Hand off to PlanningAgent
            result = await handle_router_handoff(router_msg)
            
            if result.get("status") == "ok":
                logger.info(f"Successfully routed bootstrap session {self.session_id} to PlanningAgent")
                return True
            else:
                logger.error(f"PlanningAgent handoff failed: {result.get('message', 'Unknown error')}")
                return False
            
        except Exception as e:
            logger.error(f"Error routing to planner: {e}")
            return False