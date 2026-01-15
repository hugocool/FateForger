"""Calendar Haunter - AutoGen MCP Calendar integration for FateForger."""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import Any, List, Optional, Union

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

from ...core.config import settings
from ...core.logging import get_logger
from .base import BaseHaunter

logger = get_logger(__name__)


class CalendarHaunter(BaseHaunter):
    """AutoGen-powered calendar haunter with real Google Calendar access via MCP.

    Uses StreamableHttpServerParams to bypass broken SSE transport,
    providing full calendar functionality through MCP protocol.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._agent: Optional[AssistantAgent] = None
        self._mcp_server_url = os.getenv(
            "MCP_CALENDAR_SERVER_URL", "http://localhost:3000"
        )

    async def _ensure_agent(self) -> AssistantAgent:
        """Lazy-load the AutoGen MCP calendar agent."""
        if self._agent is None:
            self._agent = await self._create_calendar_agent()
        return self._agent

    async def _create_calendar_agent(self) -> AssistantAgent:
        """Create AutoGen agent with real Google Calendar MCP tools via HTTP transport.

        Returns:
            Fully configured AssistantAgent with 9 Google Calendar tools

        Raises:
            RuntimeError: If MCP server is unavailable or tools can't be loaded
        """
        try:
            logger.info("🔧 Configuring MCP HTTP transport")

            # Use HTTP transport (bypasses broken SSE)
            params = StreamableHttpServerParams(
                url=self._mcp_server_url,
                timeout=10.0,
            )

            if not settings.openai_api_key:
                raise RuntimeError("OpenAI API key not configured")

            logger.info(f"📡 Loading calendar tools from {self._mcp_server_url}")
            tools = await mcp_server_tools(params)

            if not tools:
                raise RuntimeError(
                    "No MCP calendar tools loaded - server may be unavailable"
                )

            logger.info(
                f"🛠️ Loaded {len(tools)} calendar tools: {[getattr(t, 'name', str(t)[:30]) for t in tools[:3]]}..."
            )

            agent = AssistantAgent(
                name="CalendarHaunter",
                model_client=OpenAIChatCompletionClient(
                    model="gpt-4o-mini", api_key=settings.openai_api_key
                ),
                system_message=(
                    f"You are a calendar haunter in the FateForger system. "
                    f"Today is {dt.date.today().isoformat()}. "
                    f"Use your Google Calendar tools to help users manage their schedules. "
                    f"Be helpful, conversational, and proactive in suggesting calendar improvements."
                ),
                tools=tools,  # type: ignore - MCP tools are compatible
            )

            logger.info("✅ Calendar haunter agent created successfully")
            return agent

        except Exception as e:
            logger.error(f"Failed to create calendar agent: {e}")
            raise RuntimeError(f"Calendar haunter initialization failed: {e}") from e

    async def ask_calendar_question(self, question: str) -> str:
        """Ask the calendar agent a question and return the response.

        Args:
            question: Natural language question about calendar

        Returns:
            Agent's response as plain text

        Raises:
            RuntimeError: If agent fails to respond
        """
        try:
            agent = await self._ensure_agent()

            logger.info(f"❓ Calendar question: {question}")

            message = TextMessage(content=question, source="user")
            response = await agent.on_messages([message], CancellationToken())

            # Extract text content from response
            content = getattr(
                response.chat_message, "content", str(response.chat_message)
            )
            if isinstance(content, list) and content:
                # Handle structured response format
                text_parts = [
                    item.get("text", str(item))
                    for item in content
                    if isinstance(item, dict)
                ]
                answer = "\n".join(text_parts) if text_parts else str(content[0])
            else:
                answer = str(content)

            logger.info(f"💬 Calendar response ({len(answer)} chars)")
            return answer

        except Exception as e:
            logger.error(f"Calendar question failed: {e}")
            raise RuntimeError(f"Failed to get calendar response: {e}") from e

    async def get_todays_events(self) -> str:
        """Get today's calendar events."""
        today = dt.date.today().isoformat()
        return await self.ask_calendar_question(
            f"What events do I have today ({today})?"
        )

    async def get_weekly_schedule(self) -> str:
        """Get this week's calendar schedule."""
        return await self.ask_calendar_question(
            "What's my schedule looking like this week?"
        )

    async def list_calendars(self) -> str:
        """List available calendars."""
        return await self.ask_calendar_question(
            "Can you list all my Google Calendar calendars?"
        )

    async def search_events(self, query: str) -> str:
        """Search for events containing specific terms."""
        return await self.ask_calendar_question(
            f"Search my calendar for events containing: {query}"
        )

    async def create_event(
        self, title: str, start_time: str, description: Optional[str] = None
    ) -> str:
        """Create a new calendar event."""
        event_details = f"Create a calendar event titled '{title}' at {start_time}"
        if description:
            event_details += f" with description: {description}"
        return await self.ask_calendar_question(event_details)

    async def handle_reply(self, text: str) -> None:
        """Handle user replies - forward to calendar agent."""
        try:
            response = await self.ask_calendar_question(text)
            await self.send(f"📅 Calendar Assistant: {response}")

        except Exception as e:
            logger.error(f"Failed to handle calendar reply: {e}")
            await self.send(
                "❌ Sorry, I'm having trouble accessing your calendar right now."
            )


async def create_calendar_haunter_agent() -> AssistantAgent:
    """Standalone function to create calendar agent for testing/external use.

    Returns:
        Configured AssistantAgent with Google Calendar tools
    """
    mcp_server_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable not set")

    # Configure HTTP transport (bypasses broken SSE)
    params = StreamableHttpServerParams(url=mcp_server_url, timeout=10.0)

    # Fetch real Google Calendar tools
    tools = await mcp_server_tools(params)

    if not tools:
        raise RuntimeError(f"No tools loaded from MCP server at {mcp_server_url}")

    # Create AutoGen agent with real tools
    return AssistantAgent(
        name="CalendarAgent",
        model_client=OpenAIChatCompletionClient(
            model="gpt-4o-mini", api_key=openai_api_key
        ),
        system_message=f"You are a calendar assistant. Today is {dt.date.today().isoformat()}.",
        tools=tools,  # type: ignore - MCP tools are compatible
    )
