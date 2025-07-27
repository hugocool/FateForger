"""
Daily Planning Event Detection System

This module implements the daily APScheduler job to check for missing 'plan tomorrow' events
and trigger the bootstrapping workflow when needed.
"""

import logging
import os
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from ..config import Config
from ..database import get_db_session
from ..mcp.workbench import McpWorkbench
from ..models import PlanningBootstrapSession, PlanStatus
from ..planner_bot import PlannerBot

logger = logging.getLogger(__name__)


class DailyPlannerChecker:
    """
    Daily checker that looks for missing 'plan tomorrow' events and initiates
    the modular bootstrapping workflow.
    """

    def __init__(self, config: Config):
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self.mcp_workbench = None
        self.planner_bot = None

        # Configurable check time (default 17:00)
        self.check_hour = int(os.getenv("DAILY_PLAN_CHECK_HOUR", "17"))
        self.check_minute = int(os.getenv("DAILY_PLAN_CHECK_MINUTE", "0"))

    async def initialize(self):
        """Initialize MCP workbench and planner bot."""
        try:
            # Initialize MCP workbench for calendar access
            self.mcp_workbench = McpWorkbench()
            await self.mcp_workbench.initialize()

            # Initialize planner bot for agent handoff
            self.planner_bot = PlannerBot(self.config)

            logger.info("DailyPlannerChecker initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize DailyPlannerChecker: {e}")
            raise

    def start(self):
        """Start the daily scheduler."""
        try:
            # Schedule daily check at configured time
            self.scheduler.add_job(
                self._daily_check_job,
                trigger=CronTrigger(hour=self.check_hour, minute=self.check_minute),
                id="daily_planning_check",
                name="Daily Planning Event Check",
                replace_existing=True,
            )

            self.scheduler.start()
            logger.info(
                f"Daily planner check scheduled for {self.check_hour:02d}:{self.check_minute:02d}"
            )

        except Exception as e:
            logger.error(f"Failed to start daily scheduler: {e}")
            raise

    def stop(self):
        """Stop the daily scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Daily planner check scheduler stopped")

    async def _daily_check_job(self):
        """
        Main daily job that checks for missing planning events and triggers bootstrapping.
        """
        try:
            logger.info("Starting daily planning event check")

            # Calculate tomorrow's date
            tomorrow = datetime.now().date() + timedelta(days=1)

            # Check if tomorrow has a planning event
            has_planning_event = await self._check_for_planning_event(tomorrow)

            if not has_planning_event:
                logger.info(
                    f"No planning event found for {tomorrow}, initiating bootstrap"
                )
                await self._initiate_planning_bootstrap(tomorrow)
            else:
                logger.info(f"Planning event already exists for {tomorrow}")

        except Exception as e:
            logger.error(f"Error in daily planning check: {e}")

    async def _check_for_planning_event(self, target_date: date) -> bool:
        """
        Check if a planning event exists for the target date using MCP calendar tools.

        Args:
            target_date: The date to check for planning events

        Returns:
            True if a planning event exists, False otherwise
        """
        try:
            if not self.mcp_workbench:
                logger.error("MCP workbench not initialized")
                return False

            # Define start and end of target day for search
            start_time = datetime.combine(target_date, time.min)
            end_time = datetime.combine(target_date, time.max)

            # Search for events using MCP calendar tools
            search_params = {
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "query": "plan tomorrow",  # Look for planning-related events
            }

            # Use MCP workbench to search calendar
            events = await self.mcp_workbench.search_events(search_params)

            # Check if any events match planning criteria
            planning_keywords = ["plan", "planning", "plan tomorrow", "daily planning"]

            for event in events:
                event_title = event.get("summary", "").lower()
                if any(keyword in event_title for keyword in planning_keywords):
                    logger.info(f"Found planning event: {event.get('summary')}")
                    return True

            return False

        except Exception as e:
            logger.error(f"Error checking for planning event: {e}")
            return False

    async def _initiate_planning_bootstrap(self, target_date: date):
        """
        Initiate the modular planning bootstrap workflow for the target date.

        Args:
            target_date: The date that needs planning
        """
        try:
            # Check if we already have an active bootstrap session for this date
            async with get_db_session() as db_session:
                result = await db_session.execute(
                    select(PlanningBootstrapSession).where(
                        PlanningBootstrapSession.target_date == target_date,
                        PlanningBootstrapSession.status.in_(
                            [PlanStatus.NOT_STARTED, PlanStatus.IN_PROGRESS]
                        ),
                    )
                )
                existing_session = result.scalar_one_or_none()

                if existing_session:
                    logger.info(f"Bootstrap session already exists for {target_date}")
                    return

                # Create new bootstrap session
                bootstrap_session = PlanningBootstrapSession(
                    target_date=target_date,
                    commitment_type="PLANNING",
                    status=PlanStatus.NOT_STARTED,
                    created_at=datetime.utcnow(),
                    context={
                        "trigger": "daily_check",
                        "check_time": datetime.utcnow().isoformat(),
                        "target_date": target_date.isoformat(),
                    },
                )

                db_session.add(bootstrap_session)
                # Session will auto-commit on context exit

                logger.info(
                    f"Created bootstrap session {bootstrap_session.id} for {target_date}"
                )

                # Hand off to PlannerBot for processing
                await self._handoff_to_planner_bot(bootstrap_session)

        except Exception as e:
            logger.error(f"Error initiating planning bootstrap: {e}")

    async def _handoff_to_planner_bot(
        self, bootstrap_session: PlanningBootstrapSession
    ):
        """
        Hand off the bootstrap session to PlannerBot using Autogen patterns.

        Args:
            bootstrap_session: The session to hand off
        """
        try:
            if not self.planner_bot:
                logger.error("PlannerBot not initialized")
                return

            # Prepare handoff message with session context
            handoff_message = {
                "type": "planning_bootstrap_request",
                "session_id": bootstrap_session.id,
                "target_date": bootstrap_session.target_date.isoformat(),
                "commitment_type": bootstrap_session.commitment_type,
                "context": bootstrap_session.context,
                "instructions": f"Please initiate planning workflow for {bootstrap_session.target_date}",
            }

            # Use Autogen handoff pattern to transfer control
            await self.planner_bot.handle_bootstrap_request(handoff_message)

            # Update session status
            async with get_db_session() as db_session:
                result = await db_session.execute(
                    select(PlanningBootstrapSession).where(
                        PlanningBootstrapSession.id == bootstrap_session.id
                    )
                )
                session = result.scalar_one_or_none()
                if session:
                    session.status = PlanStatus.IN_PROGRESS
                    session.handoff_time = datetime.utcnow()
                    # Session will auto-commit on context exit

            logger.info(
                f"Successfully handed off session {bootstrap_session.id} to PlannerBot"
            )

        except Exception as e:
            logger.error(f"Error handing off to PlannerBot: {e}")

    async def manual_check(self, target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Manually trigger a planning check for testing/debugging.

        Args:
            target_date: Optional specific date to check (defaults to tomorrow)

        Returns:
            Dictionary with check results
        """
        if target_date is None:
            target_date = datetime.now().date() + timedelta(days=1)

        try:
            has_event = await self._check_for_planning_event(target_date)

            result = {
                "target_date": target_date.isoformat(),
                "has_planning_event": has_event,
                "timestamp": datetime.utcnow().isoformat(),
            }

            if not has_event:
                await self._initiate_planning_bootstrap(target_date)
                result["bootstrap_initiated"] = True
            else:
                result["bootstrap_initiated"] = False

            return result

        except Exception as e:
            logger.error(f"Error in manual check: {e}")
            return {
                "error": str(e),
                "target_date": target_date.isoformat(),
                "timestamp": datetime.utcnow().isoformat(),
            }


# Global instance for application use
daily_checker = None


async def initialize_daily_checker(config: Config) -> DailyPlannerChecker:
    """Initialize the global daily checker instance."""
    global daily_checker
    if daily_checker is None:
        daily_checker = DailyPlannerChecker(config)
        await daily_checker.initialize()
    return daily_checker


def get_daily_checker() -> DailyPlannerChecker:
    """Get the global daily checker instance."""
    global daily_checker
    if daily_checker is None:
        raise RuntimeError(
            "Daily checker not initialized. Call initialize_daily_checker first."
        )
    return daily_checker
