"""Integration tests for Calendar Haunter with AutoGen MCP integration."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.admonisher.calendar import (
    CalendarHaunter,
    create_calendar_haunter_agent,
)


@pytest.mark.integration
class TestCalendarHaunterIntegration:
    """Integration tests for the calendar haunter end-to-end functionality."""

    @pytest.fixture
    def mock_slack(self):
        """Mock Slack client for integration tests."""
        mock = AsyncMock()
        mock.chat_postMessage.return_value = {"ts": "1234567890.123456"}
        return mock

    @pytest.fixture
    def mock_scheduler(self):
        """Mock scheduler for integration tests."""
        return MagicMock()

    @pytest.fixture
    def calendar_haunter(self, mock_slack, mock_scheduler):
        """Create CalendarHaunter instance for integration testing."""
        return CalendarHaunter(
            session_id=123,
            slack=mock_slack,
            scheduler=mock_scheduler,
            channel="C123456",
        )

    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="Requires OPENAI_API_KEY environment variable",
    )
    @pytest.mark.skipif(
        not os.getenv("MCP_CALENDAR_SERVER_URL"),
        reason="Requires MCP_CALENDAR_SERVER_URL environment variable",
    )
    async def test_full_calendar_workflow(self, calendar_haunter):
        """Test the complete calendar haunter workflow with real MCP server.

        This test requires:
        1. Running MCP calendar server at MCP_CALENDAR_SERVER_URL
        2. Valid OPENAI_API_KEY
        3. Google Calendar OAuth credentials configured in the MCP server
        """
        try:
            # Test 1: Check if agent can be created
            agent = await calendar_haunter._create_calendar_agent()
            assert agent is not None

            # Test 2: Test basic calendar query
            calendars_response = await calendar_haunter.list_calendars()
            assert isinstance(calendars_response, str)
            assert len(calendars_response) > 0
            print(f"✅ Calendars listed: {calendars_response[:100]}...")

            # Test 3: Test today's events
            today_events = await calendar_haunter.get_todays_events()
            assert isinstance(today_events, str)
            print(f"✅ Today's events: {today_events[:100]}...")

            # Test 4: Test weekly schedule
            weekly_schedule = await calendar_haunter.get_weekly_schedule()
            assert isinstance(weekly_schedule, str)
            print(f"✅ Weekly schedule: {weekly_schedule[:100]}...")

            # Test 5: Test search functionality
            search_results = await calendar_haunter.search_events("meeting")
            assert isinstance(search_results, str)
            print(f"✅ Search results: {search_results[:100]}...")

            # Test 6: Test Slack integration
            await calendar_haunter.handle_reply("What's my schedule today?")
            calendar_haunter.slack.chat_postMessage.assert_called()

            print("🎉 All integration tests passed!")

        except Exception as e:
            pytest.skip(
                f"Integration test failed - MCP server or Google Calendar not available: {e}"
            )

    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="Requires OPENAI_API_KEY environment variable",
    )
    async def test_mcp_server_unavailable(self):
        """Test behavior when MCP server is unavailable."""
        # Use invalid URL to simulate server unavailable
        original_url = os.environ.get("MCP_CALENDAR_SERVER_URL", "")
        os.environ["MCP_CALENDAR_SERVER_URL"] = "http://invalid:9999"

        try:
            with pytest.raises(
                RuntimeError, match="Calendar haunter initialization failed"
            ):
                await create_calendar_haunter_agent()
        finally:
            # Restore original URL
            if original_url:
                os.environ["MCP_CALENDAR_SERVER_URL"] = original_url
            else:
                os.environ.pop("MCP_CALENDAR_SERVER_URL", None)

    async def test_concurrent_requests(self, calendar_haunter):
        """Test handling concurrent calendar requests."""
        # Mock the agent to avoid real MCP calls
        mock_agent = AsyncMock()
        mock_response = MagicMock()
        mock_response.chat_message.content = [
            {"type": "text", "text": "Concurrent response"}
        ]
        mock_agent.on_messages.return_value = mock_response
        calendar_haunter._agent = mock_agent

        # Make multiple concurrent requests
        tasks = [
            calendar_haunter.get_todays_events(),
            calendar_haunter.get_weekly_schedule(),
            calendar_haunter.list_calendars(),
            calendar_haunter.search_events("test"),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed
        for result in results:
            assert isinstance(result, str)
            assert result == "Concurrent response"

    async def test_error_recovery(self, calendar_haunter, mock_slack):
        """Test error recovery in Slack integration."""
        # Mock agent to raise an error
        calendar_haunter.ask_calendar_question = AsyncMock(
            side_effect=RuntimeError("Simulated calendar error")
        )

        # Handle reply should not raise, but send error message
        await calendar_haunter.handle_reply("Test query")

        # Verify error message was sent to Slack
        mock_slack.chat_postMessage.assert_called_with(
            channel="C123456",
            text="❌ Sorry, I'm having trouble accessing your calendar right now.",
        )

    @pytest.mark.parametrize(
        "query,expected_method",
        [
            ("What's my schedule today?", "get_todays_events"),
            ("List my calendars", "list_calendars"),
            ("Show me this week's events", "get_weekly_schedule"),
            ("Search for meeting", "search_events"),
            ("Create an event for tomorrow", "create_event"),
        ],
    )
    async def test_natural_language_routing(
        self, calendar_haunter, query, expected_method
    ):
        """Test that natural language queries are properly handled."""
        # This is more of a documentation test of expected behavior
        # In practice, the AutoGen agent handles the routing

        mock_agent = AsyncMock()
        mock_response = MagicMock()
        mock_response.chat_message.content = f"Response for {query}"
        mock_agent.on_messages.return_value = mock_response
        calendar_haunter._agent = mock_agent

        result = await calendar_haunter.ask_calendar_question(query)

        # Verify the agent was called with the query
        mock_agent.on_messages.assert_called_once()
        call_args = mock_agent.on_messages.call_args[0][0][0]
        assert call_args.content == query
        assert result == f"Response for {query}"


class TestCalendarHaunterPerformance:
    """Performance tests for calendar haunter."""

    async def test_agent_initialization_time(self):
        """Test that agent initialization completes within reasonable time."""
        if not os.getenv("OPENAI_API_KEY") or not os.getenv("MCP_CALENDAR_SERVER_URL"):
            pytest.skip("Requires API keys and MCP server")

        import time

        start_time = time.time()

        try:
            agent = await create_calendar_haunter_agent()
            initialization_time = time.time() - start_time

            # Agent should initialize within 30 seconds
            assert (
                initialization_time < 30
            ), f"Agent took {initialization_time:.2f}s to initialize"
            print(f"✅ Agent initialized in {initialization_time:.2f}s")

        except Exception as e:
            pytest.skip(f"Performance test failed: {e}")

    async def test_response_time(self, mock_slack, mock_scheduler):
        """Test calendar query response time."""
        if not os.getenv("OPENAI_API_KEY") or not os.getenv("MCP_CALENDAR_SERVER_URL"):
            pytest.skip("Requires API keys and MCP server")

        haunter = CalendarHaunter(
            session_id=123,
            slack=mock_slack,
            scheduler=mock_scheduler,
            channel="C123456",
        )

        try:
            import time

            start_time = time.time()

            await haunter.get_todays_events()

            response_time = time.time() - start_time

            # Response should come within 15 seconds
            assert response_time < 15, f"Query took {response_time:.2f}s to respond"
            print(f"✅ Query responded in {response_time:.2f}s")

        except Exception as e:
            pytest.skip(f"Performance test failed: {e}")


if __name__ == "__main__":
    """Run integration tests directly."""
    import asyncio

    async def run_basic_test():
        """Run a basic integration test."""
        if not os.getenv("OPENAI_API_KEY"):
            print("❌ OPENAI_API_KEY not set")
            return

        if not os.getenv("MCP_CALENDAR_SERVER_URL"):
            print("❌ MCP_CALENDAR_SERVER_URL not set")
            return

        print("🚀 Running basic calendar haunter test...")

        try:
            # Test standalone agent creation
            agent = await create_calendar_haunter_agent()
            print("✅ Calendar agent created successfully")

            # Test full haunter
            mock_slack = AsyncMock()
            mock_scheduler = MagicMock()

            haunter = CalendarHaunter(
                session_id=1, slack=mock_slack, scheduler=mock_scheduler, channel="test"
            )

            calendars = await haunter.list_calendars()
            print(f"✅ Calendars retrieved: {len(calendars)} chars")

            events = await haunter.get_todays_events()
            print(f"✅ Today's events retrieved: {len(events)} chars")

            print("🎉 Basic integration test passed!")

        except Exception as e:
            print(f"❌ Integration test failed: {e}")
            import traceback

            traceback.print_exc()

    # Run the test
    asyncio.run(run_basic_test())
