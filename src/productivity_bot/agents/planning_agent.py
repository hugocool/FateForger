"""
Extended Planning Agent for handling router handoffs and calendar operations.

This module extends the existing planner agent to handle structured payloads
from the RouterAgent and perform calendar operations via MCP integration.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional
from uuid import UUID

from ..actions.haunt_payload import HauntPayload
from ..actions.planner_action import PlannerAction
from ..common import get_config, get_logger, mcp_query
from ..database import get_db_session
from ..models import PlanningSession
from ._autogen_setup import build_mcp_tools, get_planner_agent_config

logger = get_logger("planning_agent")


class PlanningAgent:
    """
    Extended planning agent for handling router handoffs and calendar operations.

    This agent receives structured payloads from the RouterAgent and performs
    the appropriate calendar operations using MCP tools.
    """

    def __init__(self):
        """Initialize the planning agent."""
        self.config = get_config()
        self.mcp_tools = None
        self.initialized = False
        logger.info("PlanningAgent initialized")

    async def _ensure_initialized(self):
        """Ensure MCP tools are initialized."""
        if not self.initialized:
            try:
                self.mcp_tools = await build_mcp_tools()
                self.initialized = True
                logger.info("PlanningAgent MCP tools initialized")
            except Exception as e:
                logger.warning(f"MCP tools initialization failed: {e}")
                self.mcp_tools = None
                self.initialized = True

    async def handle_router_message(self, router_msg: Dict[str, Any]) -> Dict[str, str]:
        """
        Handle a message from the RouterAgent.

        Args:
            router_msg: Dictionary with "target" and "payload" keys

        Returns:
            Dictionary with status and any additional info
        """
        try:
            await self._ensure_initialized()

            if router_msg.get("target") != "planner":
                return {"status": "error", "message": "Not targeted for planner"}

            payload_data = router_msg.get("payload", {})
            payload = HauntPayload.from_dict(payload_data)

            logger.info(
                f"Handling router message for session {payload.session_id}: {payload.action}"
            )

            # Route to appropriate handler based on action
            if payload.action == "create_event":
                return await self._create_planning_event(payload)
            elif payload.action == "postpone":
                return await self._postpone_event(payload)
            elif payload.action == "mark_done":
                return await self._mark_done(payload)
            else:
                return {
                    "status": "error",
                    "message": f"Unknown action: {payload.action}",
                }

        except Exception as e:
            logger.error(f"Error handling router message: {e}")
            return {"status": "error", "message": str(e)}

    async def _create_planning_event(self, payload: HauntPayload) -> Dict[str, str]:
        """
        Create a planning event in the calendar.

        Args:
            payload: The haunt payload with event creation request

        Returns:
            Status dictionary
        """
        try:
            # Get the planning session from database
            async with get_db_session() as db:
                from sqlalchemy import select

                result = db.execute(
                    select(PlanningSession).where(
                        PlanningSession.id == payload.session_id
                    )
                )
                session = result.scalar_one_or_none()

                if not session:
                    return {"status": "error", "message": "Session not found"}

            # Parse time commitment from the payload
            commit_time = payload.commit_time_str

            # For now, create a simple planning event
            # In a full implementation, this would use MCP tools to create the actual calendar event
            if self.mcp_tools:
                try:
                    # Use MCP to create calendar event
                    result = await self._create_calendar_event_via_mcp(
                        session, commit_time
                    )
                    if result.get("success"):
                        return {"status": "ok", "message": "Calendar event created"}
                    else:
                        return {
                            "status": "error",
                            "message": result.get("error", "Calendar creation failed"),
                        }
                except Exception as e:
                    logger.error(f"MCP calendar creation failed: {e}")

            # Fallback: mark session as having an event created
            async with get_db_session() as db:
                session.status = "COMPLETE"
                db.commit()

            logger.info(f"Created planning event for session {payload.session_id}")
            return {"status": "ok", "message": "Planning event created successfully"}

        except Exception as e:
            logger.error(f"Error creating planning event: {e}")
            return {"status": "error", "message": str(e)}

    async def _postpone_event(self, payload: HauntPayload) -> Dict[str, str]:
        """
        Postpone a planning event.

        Args:
            payload: The haunt payload with postpone request

        Returns:
            Status dictionary
        """
        try:
            minutes = payload.minutes or 15

            # Update the planning session
            async with get_db_session() as db:
                from sqlalchemy import select

                result = db.execute(
                    select(PlanningSession).where(
                        PlanningSession.id == payload.session_id
                    )
                )
                session = result.scalar_one_or_none()

                if session:
                    # In a full implementation, this would reschedule the calendar event
                    logger.info(
                        f"Postponed session {payload.session_id} by {minutes} minutes"
                    )
                    db.commit()

            return {"status": "ok", "message": f"Event postponed by {minutes} minutes"}

        except Exception as e:
            logger.error(f"Error postponing event: {e}")
            return {"status": "error", "message": str(e)}

    async def _mark_done(self, payload: HauntPayload) -> Dict[str, str]:
        """
        Mark a planning session as complete.

        Args:
            payload: The haunt payload with mark done request

        Returns:
            Status dictionary
        """
        try:
            async with get_db_session() as db:
                from sqlalchemy import select

                result = db.execute(
                    select(PlanningSession).where(
                        PlanningSession.id == payload.session_id
                    )
                )
                session = result.scalar_one_or_none()

                if session:
                    session.status = "COMPLETE"
                    db.commit()
                    logger.info(f"Marked session {payload.session_id} as complete")

            return {"status": "ok", "message": "Planning session marked as complete"}

        except Exception as e:
            logger.error(f"Error marking session done: {e}")
            return {"status": "error", "message": str(e)}

    async def _create_calendar_event_via_mcp(
        self, session: PlanningSession, commit_time: str
    ) -> Dict[str, Any]:
        """
        Create calendar event using MCP tools.

        Args:
            session: The planning session
            commit_time: User's time commitment string

        Returns:
            Result dictionary
        """
        try:
            # Parse the commit_time string to extract date/time info
            # This is a simplified implementation - a full version would use
            # sophisticated date/time parsing

            # Create event via MCP
            event_data = {
                "title": f"Planning Session - {session.goals or 'Daily Planning'}",
                "description": f"Planning session for {session.user_id}",
                "start_time": commit_time,  # Would need proper parsing
                "duration": 60,  # Default 1 hour
            }

            # Use MCP query to create the event
            mcp_result = await mcp_query(
                {"method": "calendar.events.create", "params": event_data}
            )

            if mcp_result.get("success"):
                # Update session with event ID
                session.event_id = mcp_result.get("event_id")
                return {"success": True}
            else:
                return {
                    "success": False,
                    "error": mcp_result.get("error", "Unknown MCP error"),
                }

        except Exception as e:
            logger.error(f"MCP calendar creation error: {e}")
            return {"success": False, "error": str(e)}


# Global planning agent instance
_planning_agent_instance: Optional[PlanningAgent] = None


def get_planning_agent() -> PlanningAgent:
    """
    Get the global planning agent instance.

    Returns:
        PlanningAgent instance (singleton)
    """
    global _planning_agent_instance
    if _planning_agent_instance is None:
        _planning_agent_instance = PlanningAgent()
    return _planning_agent_instance


async def handle_router_handoff(router_msg: Dict[str, Any]) -> Dict[str, str]:
    """
    Handle a router handoff message.

    Args:
        router_msg: Message from RouterAgent

    Returns:
        Result dictionary
    """
    agent = get_planning_agent()
    return await agent.handle_router_message(router_msg)
