"""
Planner Agent - Structured JSON output calendar planning agent for AutoGen Sequential Workflow.

Implements Ticket #2: Uses json_output=PlanDiff for structured output and integrates
list-events MCP tool for diff-against-calendar logic.
"""

import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_core.tools import FunctionTool
from autogen_ext.tools.mcp import mcp_server_tools

from fateforger.contracts import CalendarEvent, CalendarOp, OpType, PlanDiff
from fateforger.core.config import settings
from fateforger.llm import (
    assert_strict_tools_for_structured_output,
    build_autogen_chat_client,
)
from ...tools_config import get_calendar_mcp_params

load_dotenv()


# TODO: use deepdiff for this instead
class PlannerAgentFactory:
    """Factory for creating PlannerAgent with structured output and calendar diffing."""

    @staticmethod
    async def create() -> AssistantAgent:
        """
        Create PlannerAgent with structured JSON output and calendar tools.

        Returns:
            AssistantAgent configured with json_output=PlanDiff and list-events tool
        """
        if not (settings.openrouter_api_key or settings.openai_api_key):
            raise RuntimeError(
                "No LLM API key configured. Set OPENAI_API_KEY or OPENROUTER_API_KEY."
            )

        # Load MCP calendar tools
        params = get_calendar_mcp_params(timeout=10.0)
        tools = await mcp_server_tools(params)

        # Find the list-events tool
        raw_list_events_tool = next(
            (
                tool
                for tool in tools
                if hasattr(tool, "name") and tool.name == "list-events"
            ),
            None,
        )
        if not raw_list_events_tool:
            raise RuntimeError("list-events tool not found in MCP server tools")

        async def list_events(
            calendarId: str,
            timeMin: str,
            timeMax: str,
            singleEvents: bool,
            orderBy: str,
        ) -> dict[str, Any]:
            """Strict wrapper around MCP `list-events` with explicit JSON parsing."""
            result = await raw_list_events_tool.run_json(
                {
                    "calendarId": calendarId,
                    "timeMin": timeMin,
                    "timeMax": timeMax,
                    "singleEvents": singleEvents,
                    "orderBy": orderBy,
                },
                CancellationToken(),
            )
            if isinstance(result, dict):
                return result
            text_payload = raw_list_events_tool.return_value_as_string(result)
            if not text_payload:
                raise RuntimeError("list-events returned empty payload")
            try:
                decoded = json.loads(text_payload)
            except Exception as exc:
                raise RuntimeError(
                    f"list-events returned non-JSON payload: {text_payload}"
                ) from exc
            if isinstance(decoded, list) and decoded:
                first = decoded[0]
                if isinstance(first, dict) and first.get("type") == "text":
                    text = first.get("text")
                    if isinstance(text, str):
                        try:
                            parsed = json.loads(text)
                        except Exception as exc:
                            raise RuntimeError(
                                f"list-events text payload is non-JSON: {text}"
                            ) from exc
                        if isinstance(parsed, dict):
                            return parsed
            if isinstance(decoded, dict):
                return decoded
            raise RuntimeError(
                f"list-events returned unexpected payload shape: {type(decoded).__name__}"
            )

        strict_list_events_tool = FunctionTool(
            list_events,
            name="list_events",
            description=(
                "List calendar events in a time range. Returns JSON with events/items."
            ),
            strict=True,
        )

        # Create agent with structured output
        assert_strict_tools_for_structured_output(
            tools=[strict_list_events_tool],
            output_content_type=PlanDiff,
            agent_name="PlannerAgent",
        )
        agent = AssistantAgent(
            name="PlannerAgent",
            model_client=build_autogen_chat_client(
                "planner_agent", parallel_tool_calls=False
            ),
            tools=[strict_list_events_tool],
            output_content_type=PlanDiff,  # Structured output into PlanDiff
            system_message="""
You are PlannerAgent for calendar planning with structured JSON output.

WORKFLOW:
1. You receive desired_slots as JSON: a list of CalendarEvent objects the user wants on their calendar
2. FIRST: Call the list_events tool to fetch current calendar events (use calendarId="primary")
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


# TODO: replace with deepdiff
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
