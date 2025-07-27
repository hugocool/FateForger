"""
Planner Agent - Structured JSON output calendar planning agent for AutoGen Sequential Workflow.

Implements Ticket #2: Uses json_output=PlanDiff for structured output and integrates
list-events MCP tool for diff-against-calendar logic.
"""

import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import mcp_server_tools

from ..contracts import CalendarEvent, CalendarOp, OpType, PlanDiff
from ..tools_config import get_calendar_mcp_params

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class PlannerAgentFactory:
    """Factory for creating PlannerAgent with structured output and calendar diffing."""

    @staticmethod
    async def create() -> AssistantAgent:
        """
        Create PlannerAgent with structured JSON output and calendar tools.

        Returns:
            AssistantAgent configured with json_output=PlanDiff and list-events tool
        """
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is required")

        # Load MCP calendar tools
        params = get_calendar_mcp_params(timeout=10.0)
        tools = await mcp_server_tools(params)

        # Find the list-events tool
        list_events_tool = next(
            (
                tool
                for tool in tools
                if hasattr(tool, "name") and tool.name == "list-events"
            ),
            None,
        )
        if not list_events_tool:
            raise RuntimeError("list-events tool not found in MCP server tools")

        # Create agent with structured output
        agent = AssistantAgent(
            name="PlannerAgent",
            model_client=OpenAIChatCompletionClient(
                model="gpt-4o-mini", api_key=OPENAI_API_KEY
            ),
            tools=[list_events_tool],
            output_content_type=PlanDiff,  # Structured output into PlanDiff
            system_message="""
You are PlannerAgent for calendar planning with structured JSON output.

WORKFLOW:
1. You receive desired_slots as JSON: a list of CalendarEvent objects the user wants on their calendar
2. FIRST: Call the list-events tool to fetch current calendar events (use calendarId="primary")
3. THEN: Compute a PlanDiff by comparing desired_slots to current events
4. RETURN: Only the PlanDiff JSON structure - no extra text or explanation

DIFF LOGIC:
- CREATE: desired events not in current calendar (match by id, or by summary+start time if no id)
- UPDATE: events with same id but different fields (summary, start, end, etc.)  
- DELETE: current events not in desired_slots

TIME RANGE: Use timeMin/timeMax in list-events to cover the span of desired_slots.

OUTPUT FORMAT: Return ONLY the PlanDiff JSON structure matching this schema:
{
  "operations": [
    {
      "op": "create|update|delete",
      "event": {...},      // for CREATE
      "event_id": "...",   // for UPDATE/DELETE  
      "diff": {...}        // for UPDATE
    }
  ]
}

NO prose, NO explanations - just the JSON.
""",
        )

        return agent

    @staticmethod
    async def plan_calendar_changes(
        agent: AssistantAgent, desired_slots: List[CalendarEvent]
    ) -> PlanDiff:
        """
        Get a PlanDiff from the agent given desired calendar slots.

        Args:
            agent: PlannerAgent instance from create()
            desired_slots: List of CalendarEvent objects representing desired calendar state

        Returns:
            PlanDiff with operations to transform calendar to match desired_slots
        """
        # Convert desired slots to JSON for the agent
        desired_slots_json = json.dumps(
            [event.model_dump(by_alias=True) for event in desired_slots],
            default=str,
            indent=2,
        )

        # Send planning request
        message = TextMessage(content=f"PLAN: {desired_slots_json}", source="user")

        response = await agent.on_messages([message], CancellationToken())

        # Handle the response - with output_content_type, it should be structured
        try:
            # Try to extract content from response
            if hasattr(response, "chat_message") and hasattr(
                response.chat_message, "content"
            ):
                content = getattr(response.chat_message, "content")
                if isinstance(content, PlanDiff):
                    return content
                elif isinstance(content, str):
                    # Fallback: parse JSON string to PlanDiff
                    return PlanDiff.model_validate_json(content)
                else:
                    # Try to convert to PlanDiff
                    return PlanDiff.model_validate(content)
            elif isinstance(response, PlanDiff):
                return response
            else:
                # Last resort: try to validate the response as PlanDiff
                return PlanDiff.model_validate(response)  # type: ignore
        except Exception as e:
            raise RuntimeError(
                f"Failed to extract PlanDiff from agent response: {e}"
            ) from e


def compute_time_range(desired_slots: List[CalendarEvent]) -> tuple[str, str]:
    """
    Compute timeMin/timeMax ISO strings from desired slots.

    Args:
        desired_slots: List of CalendarEvent objects

    Returns:
        Tuple of (timeMin, timeMax) in ISO 8601 format
    """
    if not desired_slots:
        # Default to current week if no slots
        now = datetime.now(timezone.utc)
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min.replace(hour=23, minute=59, second=59)
    else:
        # Find min/max times from slots
        start_times = []
        end_times = []

        for slot in desired_slots:
            if slot.start and slot.start.date_time:
                start_times.append(slot.start.date_time)
            if slot.end and slot.end.date_time:
                end_times.append(slot.end.date_time)

        if start_times and end_times:
            time_min = min(start_times)
            time_max = max(end_times)
        else:
            # Fallback to current day
            now = datetime.now(timezone.utc)
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min.replace(hour=23, minute=59, second=59)

    return time_min.isoformat(), time_max.isoformat()


def compute_plan_diff(
    desired_slots: List[CalendarEvent], current_events: List[Dict[str, Any]]
) -> PlanDiff:
    """
    Compute PlanDiff operations to transform current calendar to match desired slots.

    This is a reference implementation of the diff algorithm. The actual diffing
    should be done by the LLM agent for better context awareness.

    Args:
        desired_slots: Desired calendar state
        current_events: Current calendar events from list-events tool

    Returns:
        PlanDiff with required operations
    """
    operations = []

    # Index current events by ID
    current_index = {
        event.get("id"): event for event in current_events if event.get("id")
    }

    # Track desired event IDs
    desired_ids = {slot.id for slot in desired_slots if slot.id}

    # 1. Detect CREATES and UPDATES
    for desired in desired_slots:
        if desired.id and desired.id in current_index:
            # Potential UPDATE - compare fields
            current = current_index[desired.id]
            diff = {}

            # Compare key fields
            if desired.summary != current.get("summary"):
                diff["summary"] = desired.summary
            if desired.description != current.get("description"):
                diff["description"] = desired.description
            if desired.location != current.get("location"):
                diff["location"] = desired.location

            # Add more field comparisons as needed

            if diff:
                operations.append(
                    CalendarOp(op=OpType.UPDATE, event_id=desired.id, diff=diff)
                )  # type: ignore - all fields are optional in CalendarOp
        else:
            # CREATE - event doesn't exist
            operations.append(
                CalendarOp(op=OpType.CREATE, event=desired)
            )  # type: ignore - all fields are optional in CalendarOp

    # 2. Detect DELETES
    for existing_id, existing_event in current_index.items():
        if existing_id not in desired_ids:
            operations.append(
                CalendarOp(op=OpType.DELETE, event_id=existing_id)
            )  # type: ignore - all fields are optional in CalendarOp

    return PlanDiff(operations=operations)
