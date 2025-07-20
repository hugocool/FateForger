import asyncio
import datetime as dt
import os

import nest_asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import mcp_server_tools
from dotenv import find_dotenv, load_dotenv

# Import the new data contracts and runtime for Ticket #1
from ..contracts import CalendarEvent, CalendarOp, OpType, PlanDiff
from ..runtime import create_workflow_runtime, sync_plan_to_calendar
from ..tools_config import get_calendar_mcp_params

nest_asyncio.apply()
load_dotenv(find_dotenv())

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


async def create_minimal_calendar_agent():
    """Create working AutoGen calendar agent in minimal code"""

    # 1. Configure HTTP transport (bypasses broken SSE) using tools_config
    params = get_calendar_mcp_params(timeout=5.0)

    # 2. Fetch real Google Calendar tools
    print("📡 Loading calendar tools...")
    tools = await mcp_server_tools(params)
    print(f"🛠️ Loaded {len(tools)} tools: {[t.name for t in tools[:3]]}...")

    # Ensure we have an API key
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY environment variable is required")

    # 3. Create AutoGen agent with real tools
    agent = AssistantAgent(
        name="CalAgent",
        model_client=OpenAIChatCompletionClient(
            model="gpt-4o-mini-2024-07-18", api_key=OPENAI_API_KEY
        ),
        system_message=(
            f"You are CalendarAgent, with MCP access to the user Google Calendar. Today is {dt.date.today().isoformat()}. "
            "• Ingest structured PlannedTask inputs (id, title, start, end, category, optional event_id). "
            # "• When passed an XML <schedule>: "
            # "  – Parse each <slot> into a PlannedTask. "
            # "  – Diff against existing calendar events to detect creates, updates, or deletes. "
            # "  – Use the calendar MCP tool to sync all changes (setting event_id on success). "
            "• Validate for conflicts, respect timezone, and apply user preferences from memory. "
            "• Use the calendar MCP tool to create, update, or delete events—updating event_id on success. "
            "• Confirm each action in clear, concise messages; surface errors or overlaps. "
            "• Ask clarifying questions if tasks conflict or details are missing. "
            "Use the available tools to answer questions and/or manage my calendar."
        ),
        tools=tools,  # type: ignore - MCP tools are compatible
    )

    return agent


async def ask_calendar_question(agent, question):
    """Ask the calendar agent a question"""
    message = TextMessage(content=question, source="user")
    response = await agent.on_messages([message], CancellationToken())
    return response.chat_message.content


# Example usage (commented out - needs to be called from async context):
# async def main():
#     print("🚀 Creating minimal AutoGen MCP calendar agent...")
#     agent = await create_minimal_calendar_agent()
#     # Use the agent here...
