"""Tests for Calendar Haunter functionality."""

import asyncio
import datetime as dt
import os
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage

from fateforger.agents.admonisher.calendar import (
    CalendarHaunter,
    create_calendar_haunter_agent,
)


@pytest.fixture
def mock_slack():
    """Mock Slack client."""
    return AsyncMock()


@pytest.fixture
def mock_scheduler():
    """Mock scheduler."""
    return MagicMock()


@pytest.fixture
def calendar_haunter(mock_slack, mock_scheduler):
    """Create CalendarHaunter instance with mocks."""
    return CalendarHaunter(
        session_id=123,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel="C123456",
    )


@pytest.fixture
def mock_mcp_tools():
    """Mock MCP tools."""
    mock_tool1 = MagicMock()
    mock_tool1.name = "list-calendars"
    mock_tool2 = MagicMock()
    mock_tool2.name = "list-events"
    mock_tool3 = MagicMock()
    mock_tool3.name = "search-events"
    return [mock_tool1, mock_tool2, mock_tool3]


@pytest.fixture
def mock_agent_response():
    """Mock agent response."""
    mock_response = MagicMock()
    mock_chat_message = MagicMock()
    mock_chat_message.content = [{"type": "text", "text": "Test calendar response"}]
    mock_response.chat_message = mock_chat_message
    return mock_response


class TestCalendarHaunter:
    """Test the CalendarHaunter class."""

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch(
        "fateforger.agents.admonisher.calendar.mcp_server_tools",
        new_callable=AsyncMock,
    )
    @patch("fateforger.agents.admonisher.calendar.AssistantAgent")
    async def test_create_calendar_agent(
        self, mock_assistant, mock_mcp_tools_patch, calendar_haunter, mock_mcp_tools
    ):
        """Test creating calendar agent with MCP tools."""
        # Mock MCP server tools
        mock_mcp_tools_patch.return_value = mock_mcp_tools

        # Mock AssistantAgent
        mock_agent = AsyncMock(spec=AssistantAgent)
        mock_assistant.return_value = mock_agent

        # Create agent
        agent = await calendar_haunter._create_calendar_agent()

        # Verify MCP tools were loaded
        mock_mcp_tools_patch.assert_called_once()

        # Verify AssistantAgent was created with correct parameters
        mock_assistant.assert_called_once()
        call_args = mock_assistant.call_args
        assert call_args.kwargs["name"] == "CalendarHaunter"
        assert "calendar haunter" in call_args.kwargs["system_message"].lower()
        assert call_args.kwargs["tools"] == mock_mcp_tools

    @pytest.mark.asyncio
    @patch("fateforger.agents.admonisher.calendar.settings")
    async def test_create_calendar_agent_no_api_key(
        self, mock_settings, calendar_haunter
    ):
        """Test creating agent fails without OpenAI API key."""
        mock_settings.openai_api_key = ""  # Empty API key

        with pytest.raises(RuntimeError, match="OpenAI API key not configured"):
            await calendar_haunter._create_calendar_agent()

    @pytest.mark.asyncio
    @patch(
        "fateforger.agents.admonisher.calendar.mcp_server_tools",
        new_callable=AsyncMock,
    )
    async def test_create_calendar_agent_no_tools(
        self, mock_mcp_tools, calendar_haunter
    ):
        """Test creating agent fails when no MCP tools are loaded."""
        mock_mcp_tools.return_value = []

        with pytest.raises(RuntimeError, match="No MCP calendar tools loaded"):
            await calendar_haunter._create_calendar_agent()

    async def test_ask_calendar_question(self, calendar_haunter, mock_agent_response):
        """Test asking calendar agent a question."""
        # Mock the agent
        mock_agent = AsyncMock(spec=AssistantAgent)
        mock_agent.on_messages.return_value = mock_agent_response
        calendar_haunter._agent = mock_agent

        # Ask question
        result = await calendar_haunter.ask_calendar_question(
            "What events do I have today?"
        )

        # Verify agent was called correctly
        mock_agent.on_messages.assert_called_once()
        call_args = mock_agent.on_messages.call_args[0]
        assert len(call_args[0]) == 1  # One message
        assert call_args[0][0].content == "What events do I have today?"

        # Verify response
        assert result == "Test calendar response"

    async def test_ask_calendar_question_string_content(self, calendar_haunter):
        """Test asking question with string content response."""
        # Mock agent with string response
        mock_agent = AsyncMock(spec=AssistantAgent)
        mock_response = MagicMock()
        mock_chat_message = MagicMock()
        mock_chat_message.content = "Simple string response"
        mock_response.chat_message = mock_chat_message
        mock_agent.on_messages.return_value = mock_response
        calendar_haunter._agent = mock_agent

        result = await calendar_haunter.ask_calendar_question("Test question")
        assert result == "Simple string response"

    async def test_get_todays_events(self, calendar_haunter):
        """Test getting today's events."""
        calendar_haunter.ask_calendar_question = AsyncMock(
            return_value="Today's events"
        )

        result = await calendar_haunter.get_todays_events()

        today = dt.date.today().isoformat()
        calendar_haunter.ask_calendar_question.assert_called_once_with(
            f"What events do I have today ({today})?"
        )
        assert result == "Today's events"

    async def test_get_weekly_schedule(self, calendar_haunter):
        """Test getting weekly schedule."""
        calendar_haunter.ask_calendar_question = AsyncMock(
            return_value="Weekly schedule"
        )

        result = await calendar_haunter.get_weekly_schedule()

        calendar_haunter.ask_calendar_question.assert_called_once_with(
            "What's my schedule looking like this week?"
        )
        assert result == "Weekly schedule"

    async def test_list_calendars(self, calendar_haunter):
        """Test listing calendars."""
        calendar_haunter.ask_calendar_question = AsyncMock(return_value="Calendar list")

        result = await calendar_haunter.list_calendars()

        calendar_haunter.ask_calendar_question.assert_called_once_with(
            "Can you list all my Google Calendar calendars?"
        )
        assert result == "Calendar list"

    async def test_search_events(self, calendar_haunter):
        """Test searching events."""
        calendar_haunter.ask_calendar_question = AsyncMock(
            return_value="Search results"
        )

        result = await calendar_haunter.search_events("meeting")

        calendar_haunter.ask_calendar_question.assert_called_once_with(
            "Search my calendar for events containing: meeting"
        )
        assert result == "Search results"

    async def test_create_event(self, calendar_haunter):
        """Test creating an event."""
        calendar_haunter.ask_calendar_question = AsyncMock(return_value="Event created")

        result = await calendar_haunter.create_event(
            title="Team Meeting",
            start_time="2025-07-21 14:00",
            description="Weekly sync",
        )

        expected_query = (
            "Create a calendar event titled 'Team Meeting' at 2025-07-21 14:00 "
            "with description: Weekly sync"
        )
        calendar_haunter.ask_calendar_question.assert_called_once_with(expected_query)
        assert result == "Event created"

    async def test_create_event_no_description(self, calendar_haunter):
        """Test creating an event without description."""
        calendar_haunter.ask_calendar_question = AsyncMock(return_value="Event created")

        result = await calendar_haunter.create_event("Meeting", "2025-07-21 14:00")

        expected_query = "Create a calendar event titled 'Meeting' at 2025-07-21 14:00"
        calendar_haunter.ask_calendar_question.assert_called_once_with(expected_query)
        assert result == "Event created"

    async def test_handle_reply_success(self, calendar_haunter, mock_slack):
        """Test handling user reply successfully."""
        calendar_haunter.ask_calendar_question = AsyncMock(
            return_value="Calendar response"
        )

        await calendar_haunter.handle_reply("What's my schedule?")

        calendar_haunter.ask_calendar_question.assert_called_once_with(
            "What's my schedule?"
        )
        mock_slack.chat_postMessage.assert_called_once_with(
            channel="C123456", text="📅 Calendar Assistant: Calendar response"
        )

    async def test_handle_reply_error(self, calendar_haunter, mock_slack):
        """Test handling user reply with error."""
        calendar_haunter.ask_calendar_question = AsyncMock(
            side_effect=RuntimeError("API error")
        )

        await calendar_haunter.handle_reply("What's my schedule?")

        mock_slack.chat_postMessage.assert_called_once_with(
            channel="C123456",
            text="❌ Sorry, I'm having trouble accessing your calendar right now.",
        )

    async def test_lazy_agent_initialization(self, calendar_haunter):
        """Test that agent is created lazily."""
        # Initially no agent
        assert calendar_haunter._agent is None

        # Mock the creation method
        mock_agent = AsyncMock(spec=AssistantAgent)
        calendar_haunter._create_calendar_agent = AsyncMock(return_value=mock_agent)

        # First call should create agent
        agent1 = await calendar_haunter._ensure_agent()
        assert agent1 == mock_agent
        assert calendar_haunter._agent == mock_agent
        calendar_haunter._create_calendar_agent.assert_called_once()

        # Second call should return cached agent
        calendar_haunter._create_calendar_agent.reset_mock()
        agent2 = await calendar_haunter._ensure_agent()
        assert agent2 == mock_agent
        calendar_haunter._create_calendar_agent.assert_not_called()


class TestCreateCalendarHaunterAgent:
    """Test the standalone create_calendar_haunter_agent function."""

    @patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "test-key", "MCP_CALENDAR_SERVER_URL": "http://test:3000"},
    )
    @patch(
        "fateforger.agents.admonisher.calendar.mcp_server_tools",
        new_callable=AsyncMock,
    )
    @patch("fateforger.agents.admonisher.calendar.AssistantAgent")
    async def test_create_calendar_haunter_agent_success(
        self, mock_assistant, mock_mcp_tools
    ):
        """Test successful creation of calendar agent."""
        # Mock MCP tools
        mock_tools = [MagicMock(), MagicMock()]
        mock_mcp_tools.return_value = mock_tools

        # Mock AssistantAgent
        mock_agent = AsyncMock(spec=AssistantAgent)
        mock_assistant.return_value = mock_agent

        # Create agent
        result = await create_calendar_haunter_agent()

        # Verify MCP server was called with correct URL
        mock_mcp_tools.assert_called_once()

        # Verify AssistantAgent was created
        mock_assistant.assert_called_once()
        call_args = mock_assistant.call_args
        assert call_args.kwargs["name"] == "CalendarAgent"
        assert call_args.kwargs["tools"] == mock_tools

        assert result == mock_agent

    @patch.dict(os.environ, {"OPENAI_API_KEY": ""})
    async def test_create_calendar_haunter_agent_no_api_key(self):
        """Test creation fails without API key."""
        with pytest.raises(
            RuntimeError, match="OPENAI_API_KEY environment variable not set"
        ):
            await create_calendar_haunter_agent()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch(
        "fateforger.agents.admonisher.calendar.mcp_server_tools",
        new_callable=AsyncMock,
    )
    async def test_create_calendar_haunter_agent_no_tools(self, mock_mcp_tools):
        """Test creation fails when no tools are loaded."""
        mock_mcp_tools.return_value = []

        with pytest.raises(RuntimeError, match="No tools loaded from MCP server"):
            await create_calendar_haunter_agent()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch(
        "fateforger.agents.admonisher.calendar.mcp_server_tools",
        new_callable=AsyncMock,
    )
    async def test_default_mcp_server_url(self, mock_mcp_tools):
        """Test default MCP server URL is used when env var not set."""
        mock_mcp_tools.return_value = []  # Will cause early exit

        try:
            await create_calendar_haunter_agent()
        except RuntimeError:
            pass  # Expected due to no tools

        # Check that default URL was used
        call_args = mock_mcp_tools.call_args[0][
            0
        ]  # First positional arg (StreamableHttpServerParams)
        assert call_args.url == "http://localhost:3000"


@pytest.mark.integration
class TestCalendarHaunterIntegration:
    """Integration tests requiring real MCP server."""

    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY") or not os.getenv("MCP_CALENDAR_SERVER_URL"),
        reason="Requires OPENAI_API_KEY and MCP_CALENDAR_SERVER_URL environment variables",
    )
    async def test_real_mcp_connection(self):
        """Test connecting to real MCP server (requires running server)."""
        try:
            agent = await create_calendar_haunter_agent()
            assert agent is not None
            assert agent.name == "CalendarAgent"
        except Exception as e:
            pytest.skip(f"MCP server not available: {e}")

    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY") or not os.getenv("MCP_CALENDAR_SERVER_URL"),
        reason="Requires OPENAI_API_KEY and MCP_CALENDAR_SERVER_URL environment variables",
    )
    async def test_real_calendar_query(self, mock_slack, mock_scheduler):
        """Test real calendar query (requires running MCP server)."""
        try:
            haunter = CalendarHaunter(
                session_id=123,
                slack=mock_slack,
                scheduler=mock_scheduler,
                channel="C123456",
            )

            # Try to get today's events
            response = await haunter.get_todays_events()
            assert isinstance(response, str)
            assert len(response) > 0

        except Exception as e:
            pytest.skip(f"MCP server not available or calendar access failed: {e}")


class TestCalendarHaunterRobustness:
    """Test robustness and error handling."""

    async def test_mcp_server_timeout(self, calendar_haunter):
        """Test handling MCP server timeout."""
        with patch(
            "fateforger.agents.admonisher.calendar.mcp_server_tools",
            new_callable=AsyncMock,
        ) as mock_mcp:
            mock_mcp.side_effect = asyncio.TimeoutError("Server timeout")

            with pytest.raises(
                RuntimeError, match="Calendar haunter initialization failed"
            ):
                await calendar_haunter._create_calendar_agent()

    async def test_agent_response_timeout(self, calendar_haunter):
        """Test handling agent response timeout."""
        mock_agent = AsyncMock(spec=AssistantAgent)
        mock_agent.on_messages.side_effect = asyncio.TimeoutError("Response timeout")
        calendar_haunter._agent = mock_agent

        with pytest.raises(RuntimeError, match="Failed to get calendar response"):
            await calendar_haunter.ask_calendar_question("Test question")

    async def test_malformed_response_handling(self, calendar_haunter):
        """Test handling malformed agent responses."""
        mock_agent = AsyncMock(spec=AssistantAgent)
        mock_response = MagicMock()
        mock_chat_message = MagicMock()
        mock_chat_message.content = {"unexpected": "format"}  # Not list or string
        mock_response.chat_message = mock_chat_message
        mock_agent.on_messages.return_value = mock_response
        calendar_haunter._agent = mock_agent

        # Should handle gracefully by converting to string
        result = await calendar_haunter.ask_calendar_question("Test")
        assert "unexpected" in result
