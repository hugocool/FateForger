"""
AutoGen Planner Agent - AI-powered planning with MCP integration.

This module provides an AutoGen-based planning agent that integrates with the
Model Context Protocol (MCP) server for calendar operations. The agent can
help users create detailed daily plans by analyzing their calendar and
suggesting optimal time slots for tasks.

Features:
    - AutoGen agent with calendar integration
    - MCP server communication for calendar CRUD operations
    - Intelligent time slot suggestions
    - Context-aware planning recommendations
    - Integration with existing planning session workflow

Example:
    ```python
    from productivity_bot.autogen_planner import AutoGenPlannerAgent

    # Initialize the agent
    agent = AutoGenPlannerAgent()

    # Generate a plan for a user
    plan = await agent.generate_daily_plan(
        user_id="U123456",
        goals="Complete project review, prepare presentation",
        preferences={"work_hours": "9-17", "break_duration": 15}
    )
    ```
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient

from .common import BaseEventService, get_config, get_logger, mcp_query
from .database import PlanningSessionService

# Import from the main models.py file, not the models/ subdirectory
from .models import PlanningSession

logger = get_logger("autogen_planner")


class MCPCalendarTool:
    """
    Tool wrapper for MCP calendar operations that can be used by AutoGen agents.

    This class provides a standardized interface for AutoGen agents to interact
    with the MCP server for calendar operations, including listing events,
    creating events, and updating existing calendar entries.
    """

    def __init__(self) -> None:
        """Initialize the MCP calendar tool."""
        self.config = get_config()
        self.service = BaseEventService()

    async def list_calendar_events(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        """
        List calendar events within a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format (default: today).
            end_date: End date in YYYY-MM-DD format (default: 7 days from start).
            max_results: Maximum number of events to return.

        Returns:
            Dictionary containing list of calendar events and metadata.
        """
        try:
            if not start_date:
                start_date = datetime.now().strftime("%Y-%m-%d")

            if not end_date:
                end_dt = datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=7)
                end_date = end_dt.strftime("%Y-%m-%d")

            # Use the existing BaseEventService to list events
            start_dt = datetime.strptime(f"{start_date}T00:00:00", "%Y-%m-%dT%H:%M:%S")
            end_dt = datetime.strptime(f"{end_date}T23:59:59", "%Y-%m-%dT%H:%M:%S")

            events = await self.service.list_events(
                start_time=start_dt, end_time=end_dt
            )

            return {
                "success": True,
                "events": events,
                "count": len(events),
                "date_range": f"{start_date} to {end_date}",
            }

        except Exception as e:
            logger.error(f"Failed to list calendar events: {e}")
            return {"success": False, "error": str(e), "events": []}

    async def create_calendar_event(
        self,
        title: str,
        start_time: str,
        end_time: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new calendar event via MCP.

        Args:
            title: Event title.
            start_time: Start time in ISO format (YYYY-MM-DDTHH:MM:SSZ).
            end_time: End time in ISO format (YYYY-MM-DDTHH:MM:SSZ).
            description: Optional event description.

        Returns:
            Dictionary containing created event data or error information.
        """
        try:
            request = {
                "method": "create_event",
                "params": {
                    "title": title,
                    "start_time": start_time,
                    "end_time": end_time,
                    "description": description or "",
                },
            }

            response = await mcp_query(request)

            if response.get("success"):
                logger.info(f"Created calendar event: {title}")
                return {
                    "success": True,
                    "event": response.get("event", {}),
                    "message": f"Successfully created event '{title}'",
                }
            else:
                return {
                    "success": False,
                    "error": response.get("error", "Unknown error creating event"),
                }

        except Exception as e:
            logger.error(f"Failed to create calendar event: {e}")
            return {"success": False, "error": str(e)}

    async def get_available_time_slots(
        self,
        date_str: str,
        duration_minutes: int = 60,
        work_start: str = "09:00",
        work_end: str = "17:00",
    ) -> Dict[str, Any]:
        """
        Find available time slots for a given date.

        Args:
            date_str: Date in YYYY-MM-DD format.
            duration_minutes: Required duration in minutes.
            work_start: Work day start time in HH:MM format.
            work_end: Work day end time in HH:MM format.

        Returns:
            Dictionary containing available time slots.
        """
        try:
            # Get events for the day
            events_response = await self.list_calendar_events(
                start_date=date_str, end_date=date_str
            )

            if not events_response.get("success"):
                return events_response

            events = events_response.get("events", [])

            # Parse work hours
            work_start_time = datetime.strptime(
                f"{date_str} {work_start}", "%Y-%m-%d %H:%M"
            )
            work_end_time = datetime.strptime(
                f"{date_str} {work_end}", "%Y-%m-%d %H:%M"
            )

            # Find gaps between events
            busy_periods = []
            for event in events:
                start = datetime.fromisoformat(
                    event.get("start_time", "").replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    event.get("end_time", "").replace("Z", "+00:00")
                )
                busy_periods.append((start, end))

            # Sort busy periods by start time
            busy_periods.sort(key=lambda x: x[0])

            # Find available slots
            available_slots = []
            current_time = work_start_time

            for start, end in busy_periods:
                # Check if there's time before this event
                if (start - current_time).total_seconds() >= duration_minutes * 60:
                    available_slots.append(
                        {
                            "start": current_time.isoformat(),
                            "end": start.isoformat(),
                            "duration_minutes": int(
                                (start - current_time).total_seconds() / 60
                            ),
                        }
                    )
                current_time = max(current_time, end)

            # Check time after last event
            if (work_end_time - current_time).total_seconds() >= duration_minutes * 60:
                available_slots.append(
                    {
                        "start": current_time.isoformat(),
                        "end": work_end_time.isoformat(),
                        "duration_minutes": int(
                            (work_end_time - current_time).total_seconds() / 60
                        ),
                    }
                )

            return {
                "success": True,
                "date": date_str,
                "available_slots": available_slots,
                "busy_periods_count": len(busy_periods),
            }

        except Exception as e:
            logger.error(f"Failed to get available time slots: {e}")
            return {"success": False, "error": str(e), "available_slots": []}


class AutoGenPlannerAgent:
    """
    AutoGen-powered planning agent with MCP calendar integration.

    This agent uses AutoGen's conversational AI capabilities combined with
    MCP calendar tools to provide intelligent daily planning assistance.
    The agent can analyze existing calendar events, suggest optimal time
    slots for tasks, and help users create comprehensive daily plans.
    """

    def __init__(self) -> None:
        """Initialize the AutoGen planner agent."""
        self.config = get_config()
        self.calendar_tool = MCPCalendarTool()

        # Initialize OpenAI client for AutoGen
        self.model_client = OpenAIChatCompletionClient(
            model="gpt-4",
            api_key=self.config.openai_api_key,
        )

        # Create the planning agent
        self.agent = AssistantAgent(
            name="PlannerAgent",
            description="An AI assistant that helps create optimal daily plans by analyzing calendar availability and user goals.",
            model_client=self.model_client,
        )

    async def generate_daily_plan(
        self,
        user_id: str,
        goals: str,
        date_str: Optional[str] = None,
        preferences: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate an optimized daily plan using AutoGen and calendar analysis.

        Args:
            user_id: Slack user ID.
            goals: User's goals for the day.
            date_str: Target date in YYYY-MM-DD format (default: today).
            preferences: User preferences like work hours, break durations, etc.

        Returns:
            Dictionary containing the generated plan and recommendations.
        """
        try:
            if not date_str:
                date_str = datetime.now().strftime("%Y-%m-%d")

            # Set default preferences
            prefs = preferences or {}
            work_start = prefs.get("work_start", "09:00")
            work_end = prefs.get("work_end", "17:00")
            break_duration = prefs.get("break_duration", 15)

            logger.info(f"Generating daily plan for user {user_id} on {date_str}")

            # Get calendar events and available slots
            calendar_data = await self.calendar_tool.list_calendar_events(
                start_date=date_str, end_date=date_str
            )

            available_slots = await self.calendar_tool.get_available_time_slots(
                date_str=date_str, work_start=work_start, work_end=work_end
            )

            # Prepare context for the agent
            context = {
                "date": date_str,
                "goals": goals,
                "existing_events": calendar_data.get("events", []),
                "available_slots": available_slots.get("available_slots", []),
                "preferences": prefs,
                "work_hours": f"{work_start} to {work_end}",
                "break_duration": break_duration,
            }

            # Create planning prompt
            planning_prompt = self._build_planning_prompt(context)

            # For now, use a simplified planning approach
            # TODO: Enhance with full AutoGen conversation when API is stabilized
            plan_content = await self._generate_simple_plan(context)

            # Try to extract structured data from the response
            structured_plan = self._parse_agent_response(plan_content)

            return {
                "success": True,
                "date": date_str,
                "user_id": user_id,
                "raw_plan": plan_content,
                "structured_plan": structured_plan,
                "calendar_context": {
                    "existing_events": len(calendar_data.get("events", [])),
                    "available_slots": len(available_slots.get("available_slots", [])),
                },
            }

        except Exception as e:
            logger.error(f"Failed to generate daily plan: {e}")
            return {
                "success": False,
                "error": str(e),
                "date": date_str,
                "user_id": user_id,
            }

    def _build_planning_prompt(self, context: Dict[str, Any]) -> str:
        """
        Build a comprehensive prompt for the planning agent.

        Args:
            context: Context dictionary with goals, calendar data, and preferences.

        Returns:
            Formatted prompt string for the AutoGen agent.
        """
        existing_events = context.get("existing_events", [])
        available_slots = context.get("available_slots", [])

        prompt = f"""
You are an expert daily planning assistant. Please create an optimized daily plan for {context['date']}.

**User Goals:**
{context['goals']}

**Work Hours:** {context['work_hours']}
**Break Duration:** {context['break_duration']} minutes

**Existing Calendar Events:**
"""

        if existing_events:
            for event in existing_events:
                start = (
                    event.get("start_time", "").split("T")[1][:5]
                    if "T" in event.get("start_time", "")
                    else "Unknown"
                )
                end = (
                    event.get("end_time", "").split("T")[1][:5]
                    if "T" in event.get("end_time", "")
                    else "Unknown"
                )
                prompt += f"- {start}-{end}: {event.get('title', 'Untitled Event')}\n"
        else:
            prompt += "- No existing calendar events\n"

        prompt += f"""
**Available Time Slots:**
"""

        if available_slots:
            for slot in available_slots:
                start = (
                    slot.get("start", "").split("T")[1][:5]
                    if "T" in slot.get("start", "")
                    else "Unknown"
                )
                end = (
                    slot.get("end", "").split("T")[1][:5]
                    if "T" in slot.get("end", "")
                    else "Unknown"
                )
                duration = slot.get("duration_minutes", 0)
                prompt += f"- {start}-{end} ({duration} minutes available)\n"
        else:
            prompt += "- No available time slots found\n"

        prompt += """

**Instructions:**
1. Create a detailed time-boxed schedule that fits within the available slots
2. Break down the user's goals into specific, actionable tasks
3. Include appropriate breaks between intense work sessions  
4. Consider task priorities and energy levels throughout the day
5. Be realistic about what can be accomplished
6. Provide specific time allocations (e.g., "09:00-10:30: Deep work on project review")

**Output Format:**
Please provide:
1. A time-boxed schedule with specific time slots
2. Task breakdown for each goal
3. Suggested break times
4. Any recommendations for optimizing the day

Make your response practical, specific, and actionable.
"""

        return prompt

    async def _generate_simple_plan(self, context: Dict[str, Any]) -> str:
        """
        Generate a simple plan based on context (temporary implementation).

        Args:
            context: Context dictionary with goals, calendar data, and preferences.

        Returns:
            Generated plan text.
        """
        goals = context.get("goals", "")
        available_slots = context.get("available_slots", [])
        existing_events = context.get("existing_events", [])

        plan_parts = [
            f"# Daily Plan for {context['date']}",
            "",
            "## Goals:",
            goals,
            "",
            "## Schedule Recommendations:",
        ]

        if available_slots:
            plan_parts.append("### Available Time Slots:")
            for i, slot in enumerate(available_slots[:5]):  # Limit to 5 slots
                start = (
                    slot.get("start", "").split("T")[1][:5]
                    if "T" in slot.get("start", "")
                    else "Unknown"
                )
                end = (
                    slot.get("end", "").split("T")[1][:5]
                    if "T" in slot.get("end", "")
                    else "Unknown"
                )
                duration = slot.get("duration_minutes", 0)
                plan_parts.append(f"- {start}-{end}: Available ({duration} minutes)")

        if existing_events:
            plan_parts.append("### Existing Events:")
            for event in existing_events:
                start = (
                    event.get("start_time", "").split("T")[1][:5]
                    if "T" in event.get("start_time", "")
                    else "Unknown"
                )
                end = (
                    event.get("end_time", "").split("T")[1][:5]
                    if "T" in event.get("end_time", "")
                    else "Unknown"
                )
                plan_parts.append(
                    f"- {start}-{end}: {event.get('title', 'Untitled Event')}"
                )

        plan_parts.extend(
            [
                "",
                "## Recommendations:",
                "- Use morning hours for deep work when energy is highest",
                "- Schedule breaks between intensive tasks",
                "- Block calendar time for important goals",
                "- Leave buffer time for unexpected tasks",
            ]
        )

        return "\n".join(plan_parts)

    def _parse_agent_response(self, response: str) -> Dict[str, Any]:
        """
        Parse the agent's response into structured data.

        Args:
            response: Raw response text from the AutoGen agent.

        Returns:
            Structured dictionary with parsed plan components.
        """
        try:
            # Extract time-boxed schedule using simple parsing
            lines = response.split("\n")
            schedule_items = []
            recommendations = []

            current_section = None

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Check for time patterns (e.g., "09:00-10:30:")
                if ":" in line and ("-" in line or "–" in line):
                    # This looks like a schedule item
                    schedule_items.append(line)
                elif line.startswith("*") or line.startswith("-"):
                    # This looks like a recommendation or task
                    recommendations.append(line.lstrip("*- "))

            return {
                "schedule_items": schedule_items,
                "recommendations": recommendations,
                "total_items": len(schedule_items),
                "parsed_successfully": True,
            }

        except Exception as e:
            logger.warning(f"Failed to parse agent response: {e}")
            return {
                "schedule_items": [],
                "recommendations": [response],  # Fallback to raw response
                "total_items": 0,
                "parsed_successfully": False,
                "parse_error": str(e),
            }

    async def enhance_planning_session(
        self,
        session: PlanningSession,
        enhance_goals: bool = True,
        suggest_schedule: bool = True,
    ) -> Dict[str, Any]:
        """
        Enhance an existing planning session with AI-generated suggestions.

        Args:
            session: The planning session to enhance.
            enhance_goals: Whether to enhance/expand the goals.
            suggest_schedule: Whether to suggest a detailed schedule.

        Returns:
            Dictionary containing enhancement suggestions.
        """
        try:
            date_str = session.date.strftime("%Y-%m-%d")

            # Get user preferences (this could be enhanced to load from database)
            preferences = {
                "work_start": "09:00",
                "work_end": "17:00",
                "break_duration": 15,
            }

            # Generate enhanced plan
            enhanced_plan = await self.generate_daily_plan(
                user_id=session.user_id,
                goals=session.goals or "Daily productivity goals",
                date_str=date_str,
                preferences=preferences,
            )

            if not enhanced_plan.get("success"):
                return enhanced_plan

            # Extract enhancements
            structured_plan = enhanced_plan.get("structured_plan", {})

            return {
                "success": True,
                "session_id": session.id,
                "original_goals": session.goals,
                "original_notes": session.notes,
                "enhanced_schedule": structured_plan.get("schedule_items", []),
                "recommendations": structured_plan.get("recommendations", []),
                "ai_suggestions": enhanced_plan.get("raw_plan", ""),
                "calendar_analysis": enhanced_plan.get("calendar_context", {}),
            }

        except Exception as e:
            logger.error(f"Failed to enhance planning session {session.id}: {e}")
            return {"success": False, "error": str(e), "session_id": session.id}
