"""
Integration tests for MCP Workbench calendar integration.

These tests verify that the MCP client can properly communicate with
the google-calendar-mcp server and perform calendar operations.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytest

from src.productivity_bot.mcp_integration import CalendarMcpClient, get_mcp_client


class TestMCPIntegration:
    """Integration tests for MCP calendar operations."""

    @pytest.fixture(scope="class")
    async def mcp_client(self) -> CalendarMcpClient:
        """Get an initialized MCP client for testing."""
        client = await get_mcp_client()
        assert client is not None, "MCP client should be available for testing"
        return client

    @pytest.mark.asyncio
    async def test_mcp_client_initialization(self):
        """Test that MCP client can be initialized properly."""
        client = CalendarMcpClient()
        result = await client.initialize()
        assert result is True, "MCP client should initialize successfully"
        assert client.workbench is not None, "Workbench should be available"
        assert len(client._tools) > 0, "Tools should be discovered"

    @pytest.mark.asyncio
    async def test_list_events(self, mcp_client: CalendarMcpClient):
        """Test listing calendar events via MCP."""
        # Test basic event listing
        events = await mcp_client.list_events()
        assert isinstance(events, list), "list_events should return a list"

        # Test with date range
        start_date = datetime.now().isoformat()
        end_date = (datetime.now() + timedelta(days=7)).isoformat()

        events_filtered = await mcp_client.list_events(
            start_date=start_date, end_date=end_date
        )
        assert isinstance(
            events_filtered, list
        ), "Filtered list_events should return a list"

    @pytest.mark.asyncio
    async def test_create_event(self, mcp_client: CalendarMcpClient):
        """Test creating a calendar event via MCP."""
        # Create a test event
        start_time = (datetime.now() + timedelta(hours=1)).isoformat()
        end_time = (datetime.now() + timedelta(hours=2)).isoformat()

        test_event = await mcp_client.create_event(
            title="Test Event - MCP Integration",
            start_time=start_time,
            end_time=end_time,
            description="This is a test event created by MCP integration tests",
        )

        assert test_event is not None, "create_event should return event data"
        assert "id" in test_event, "Created event should have an ID"
        assert (
            test_event.get("summary") == "Test Event - MCP Integration"
        ), "Event title should match"

        # Clean up by deleting the test event
        event_id = test_event["id"]
        deleted = await mcp_client.delete_event(event_id)
        assert deleted is True, "Test event should be cleaned up successfully"

    @pytest.mark.asyncio
    async def test_update_event(self, mcp_client: CalendarMcpClient):
        """Test updating a calendar event via MCP."""
        # First create an event to update
        start_time = (datetime.now() + timedelta(hours=2)).isoformat()
        end_time = (datetime.now() + timedelta(hours=3)).isoformat()

        test_event = await mcp_client.create_event(
            title="Test Event for Update",
            start_time=start_time,
            end_time=end_time,
            description="Original description",
        )

        assert test_event is not None, "Test event should be created"
        event_id = test_event["id"]

        # Update the event
        updated_event = await mcp_client.update_event(
            event_id=event_id,
            title="Updated Test Event",
            description="Updated description",
        )

        assert updated_event is not None, "update_event should return updated data"
        assert (
            updated_event.get("summary") == "Updated Test Event"
        ), "Title should be updated"

        # Clean up
        deleted = await mcp_client.delete_event(event_id)
        assert deleted is True, "Test event should be cleaned up"

    @pytest.mark.asyncio
    async def test_get_event(self, mcp_client: CalendarMcpClient):
        """Test getting a specific calendar event via MCP."""
        # Create a test event first
        start_time = (datetime.now() + timedelta(hours=3)).isoformat()
        end_time = (datetime.now() + timedelta(hours=4)).isoformat()

        test_event = await mcp_client.create_event(
            title="Test Event for Get", start_time=start_time, end_time=end_time
        )

        assert test_event is not None, "Test event should be created"
        event_id = test_event["id"]

        # Get the event by ID
        retrieved_event = await mcp_client.get_event(event_id)

        assert retrieved_event is not None, "get_event should return event data"
        assert (
            retrieved_event.get("id") == event_id
        ), "Retrieved event should have correct ID"
        assert (
            retrieved_event.get("summary") == "Test Event for Get"
        ), "Retrieved event should have correct title"

        # Clean up
        deleted = await mcp_client.delete_event(event_id)
        assert deleted is True, "Test event should be cleaned up"

    @pytest.mark.asyncio
    async def test_delete_event(self, mcp_client: CalendarMcpClient):
        """Test deleting a calendar event via MCP."""
        # Create a test event to delete
        start_time = (datetime.now() + timedelta(hours=4)).isoformat()
        end_time = (datetime.now() + timedelta(hours=5)).isoformat()

        test_event = await mcp_client.create_event(
            title="Test Event for Delete", start_time=start_time, end_time=end_time
        )

        assert test_event is not None, "Test event should be created"
        event_id = test_event["id"]

        # Delete the event
        deleted = await mcp_client.delete_event(event_id)
        assert (
            deleted is True
        ), "delete_event should return True for successful deletion"

        # Verify event is deleted by trying to get it
        retrieved_event = await mcp_client.get_event(event_id)
        assert retrieved_event is None, "Deleted event should not be retrievable"


class TestMCPWorkbenchIntegration:
    """Test MCP Workbench integration with AutoGen agents."""

    @pytest.mark.asyncio
    async def test_agent_can_use_mcp_tools(self):
        """Test that AutoGen agents can properly use MCP tools."""
        from src.productivity_bot.agents.slack_assistant_agent import (
            SlackAssistantAgent,
        )

        # Initialize agent with MCP integration
        assistant = StructuredSlackAssistant()
        await assistant.initialize()

        assert assistant.agent is not None, "Agent should be initialized"

        # Check if workbench is properly configured
        if hasattr(assistant, "workbench") and assistant.workbench:
            # Test that the agent can discover tools
            async with assistant.workbench:
                tools = await assistant.workbench.list_tools()
                assert len(tools) > 0, "Agent should have access to MCP tools"

                # Check for calendar-related tools
                tool_names = [getattr(tool, "name", str(tool)) for tool in tools]
                calendar_tools = [
                    name for name in tool_names if "calendar" in str(name).lower()
                ]
                assert (
                    len(calendar_tools) > 0
                ), "Should have calendar-related tools available"

    @pytest.mark.asyncio
    async def test_end_to_end_planning_workflow(self, mcp_client: CalendarMcpClient):
        """Test end-to-end planning workflow using MCP."""
        from datetime import date

        from src.productivity_bot.models import PlanningSession

        # Create a test planning session
        session = PlanningSession(
            user_id="test_user",
            date=date.today(),
            goals="Test MCP integration",
            scheduled_for=datetime.now() + timedelta(hours=1),
        )

        # Test recreating event via MCP
        success = await session.recreate_event()

        # Should work if MCP is properly configured
        if mcp_client.workbench:
            assert (
                success is True
            ), "Planning session should successfully recreate event via MCP"
            assert session.event_id is not None, "Event ID should be set after creation"

            # Clean up the created event
            if session.event_id:
                await mcp_client.delete_event(session.event_id)


@pytest.mark.integration
class TestMCPServerConnectivity:
    """Test MCP server connectivity and configuration."""

    @pytest.mark.asyncio
    async def test_mcp_server_reachable(self):
        """Test that the MCP server is reachable and responding."""
        client = CalendarMcpClient(mcp_server_url="http://mcp:4000")

        # This should work if the MCP server is running
        initialized = await client.initialize()

        if initialized:
            assert client.workbench is not None, "Workbench should be available"
            assert len(client._tools) > 0, "Should discover calendar tools"
        else:
            pytest.skip("MCP server not available - check Docker setup")

    @pytest.mark.asyncio
    async def test_mcp_tool_discovery(self):
        """Test that expected calendar tools are discovered."""
        client = CalendarMcpClient()

        if await client.initialize():
            # Check for expected calendar tool names
            tool_names = []
            for tool in client._tools:
                if hasattr(tool, "name"):
                    tool_names.append(tool.name)
                elif isinstance(tool, dict) and "name" in tool:
                    tool_names.append(tool["name"])

            expected_tools = [
                "calendar.events.list",
                "calendar.events.insert",
                "calendar.events.update",
                "calendar.events.delete",
            ]

            for expected_tool in expected_tools:
                matching_tools = [
                    name for name in tool_names if expected_tool in str(name)
                ]
                assert (
                    len(matching_tools) > 0
                ), f"Should discover tool matching '{expected_tool}'"
        else:
            pytest.skip("MCP server not available for tool discovery test")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
