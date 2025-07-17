"""
MCP Workbench Integration for Calendar Operations.

This module provides a properly configured MCP Workbench client for calendar operations,
replacing raw REST calls with proper agentic calendar tool usage.

Note: This is a placeholder implementation. The actual MCP tool calling mechanism
needs to be implemented based on the specific MCP server interface.
"""

import logging
from typing import Any, Dict, List, Optional

from autogen_ext.tools.mcp import McpWorkbench, SseServerParams

from .common import get_logger

logger = get_logger("mcp_integration")


class CalendarMcpClient:
    """
    MCP Workbench client for calendar operations.

    This class provides a properly configured McpWorkbench instance
    for calendar operations, ensuring all calendar interactions go
    through the MCP protocol rather than raw REST calls.

    TODO: Implement actual tool calling once MCP server interface is clarified.
    """

    def __init__(self, mcp_server_url: str = "http://mcp:4000"):
        """
        Initialize the MCP calendar client.

        Args:
            mcp_server_url: Base URL for the MCP server
        """
        self.mcp_server_url = mcp_server_url
        self.workbench: Optional[McpWorkbench] = None
        self._tools: List[Any] = []
        self._initialized = False

    async def initialize(self) -> bool:
        """
        Initialize the MCP workbench connection.

        Returns:
            True if initialization successful, False otherwise
        """
        if self._initialized:
            return True

        try:
            # Configure MCP server parameters
            server_params = SseServerParams(
                url=f"{self.mcp_server_url}/mcp", timeout=30
            )

            # Create workbench instance
            self.workbench = McpWorkbench(server_params=server_params)

            # Test connection and discover tools
            async with self.workbench:
                self._tools = await self.workbench.list_tools()

            logger.info(f"MCP Workbench initialized with {len(self._tools)} tools")

            # Log available tool names for debugging
            tool_names = []
            for tool in self._tools:
                if hasattr(tool, "name"):
                    tool_names.append(tool.name)
                elif isinstance(tool, dict) and "name" in tool:
                    tool_names.append(tool["name"])
                else:
                    tool_names.append(str(tool))

            logger.debug(f"Available MCP tools: {tool_names}")

            self._initialized = True
            return True

        except Exception as e:
            logger.error(f"Failed to initialize MCP Workbench: {e}")
            self.workbench = None
            return False

    async def list_events(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        calendar_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List calendar events using MCP.

        Args:
            start_date: Start date filter (ISO format)
            end_date: End date filter (ISO format)
            calendar_id: Specific calendar ID to filter by

        Returns:
            List of calendar events
        """
        if not await self._ensure_initialized():
            logger.warning("MCP not available, returning empty event list")
            return []

        try:
            # Prepare parameters for MCP tool call
            params = {
                "calendarId": calendar_id or "primary",
                "singleEvents": True,
                "orderBy": "startTime"
            }
            
            if start_date:
                params["timeMin"] = start_date
            if end_date:
                params["timeMax"] = end_date

            # Call MCP tool through workbench
            if not self.workbench:
                raise RuntimeError("Workbench not initialized")
                
            result = await self.workbench.call_tool("calendar.events.list", params)
                
            # Extract events from ToolResult
            events = []
            if result.result:
                for content in result.result:
                    if hasattr(content, 'content') and isinstance(content.content, str):
                        # Try to parse JSON response
                        import json
                        try:
                            data = json.loads(content.content)
                            if isinstance(data, dict) and 'items' in data:
                                events = data['items']
                            elif isinstance(data, list):
                                events = data
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse MCP response as JSON: {content.content}")
                
            logger.info(f"Retrieved {len(events)} events via MCP")
            return events

        except Exception as e:
            logger.error(f"MCP list_events failed: {e}")
            return []

    async def create_event(
        self,
        title: str,
        start_time: str,
        end_time: str,
        description: Optional[str] = None,
        location: Optional[str] = None,
        calendar_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a calendar event using MCP.

        Args:
            title: Event title
            start_time: Start time (ISO format)
            end_time: End time (ISO format)
            description: Event description
            location: Event location
            calendar_id: Target calendar ID

        Returns:
            Created event data or None if failed
        """
        if not await self._ensure_initialized():
            logger.warning("MCP not available, cannot create event")
            return None

        try:
            # Prepare event data for Google Calendar API format
            event_resource = {
                "summary": title,
                "start": {"dateTime": start_time},
                "end": {"dateTime": end_time}
            }
            
            if description:
                event_resource["description"] = description
            if location:
                event_resource["location"] = location

            params = {
                "calendarId": calendar_id or "primary",
                "resource": event_resource
            }

            # Call MCP tool
            if not self.workbench:
                raise RuntimeError("Workbench not initialized")
                
            result = await self.workbench.call_tool("calendar.events.insert", params)
            
            # Extract created event from ToolResult
            created_event = None
            if result.result:
                for content in result.result:
                    if hasattr(content, 'content') and isinstance(content.content, str):
                        import json
                        try:
                            created_event = json.loads(content.content)
                            break
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse MCP create response: {content.content}")
                
            if created_event:
                logger.info(f"Created event '{title}' via MCP with ID: {created_event.get('id')}")
            else:
                logger.warning(f"MCP create_event returned no event data")
                
            return created_event

        except Exception as e:
            logger.error(f"MCP create_event failed: {e}")
            return None

    async def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Update a calendar event using MCP.

        Args:
            event_id: Event ID to update
            title: New title
            start_time: New start time (ISO format)
            end_time: New end time (ISO format)
            description: New description
            location: New location

        Returns:
            Updated event data or None if failed
        """
        if not await self._ensure_initialized():
            logger.warning("MCP not available, cannot update event")
            return None

        try:
            # First get the existing event to preserve unchanged fields
            get_params = {
                "calendarId": "primary",
                "eventId": event_id
            }
            
            if not self.workbench:
                raise RuntimeError("Workbench not initialized")
                
            get_result = await self.workbench.call_tool("calendar.events.get", get_params)
            
            # Parse existing event
            existing_event = {}
            if get_result.result:
                for content in get_result.result:
                    if hasattr(content, 'content') and isinstance(content.content, str):
                        import json
                        try:
                            existing_event = json.loads(content.content)
                            break
                        except json.JSONDecodeError:
                            pass
            
            # Prepare update data (only include changed fields)
            event_resource = existing_event.copy()
            
            if title is not None:
                event_resource["summary"] = title
            if start_time is not None:
                event_resource["start"] = {"dateTime": start_time}
            if end_time is not None:
                event_resource["end"] = {"dateTime": end_time}
            if description is not None:
                event_resource["description"] = description
            if location is not None:
                event_resource["location"] = location

            update_params = {
                "calendarId": "primary",
                "eventId": event_id,
                "resource": event_resource
            }

            # Call MCP update tool
            result = await self.workbench.call_tool("calendar.events.update", update_params)
            
            # Extract updated event
            updated_event = None
            if result.result:
                for content in result.result:
                    if hasattr(content, 'content') and isinstance(content.content, str):
                        import json
                        try:
                            updated_event = json.loads(content.content)
                            break
                        except json.JSONDecodeError:
                            pass
                
            if updated_event:
                logger.info(f"Updated event {event_id} via MCP")
            else:
                logger.warning(f"MCP update_event returned no data for {event_id}")
                
            return updated_event

        except Exception as e:
            logger.error(f"MCP update_event failed: {e}")
            return None

    async def delete_event(self, event_id: str) -> bool:
        """
        Delete a calendar event using MCP.

        Args:
            event_id: Event ID to delete

        Returns:
            True if deletion successful, False otherwise
        """
        if not await self._ensure_initialized():
            logger.warning("MCP not available, cannot delete event")
            return False

        try:
            params = {
                "calendarId": "primary",
                "eventId": event_id
            }

            # Call MCP delete tool
            if not self.workbench:
                raise RuntimeError("Workbench not initialized")
                
            result = await self.workbench.call_tool("calendar.events.delete", params)
            
            # Check if deletion was successful (usually returns empty result on success)
            success = not result.is_error
            
            if success:
                logger.info(f"Deleted event {event_id} via MCP")
            else:
                logger.warning(f"MCP delete_event failed for {event_id}")
                
            return success

        except Exception as e:
            logger.error(f"MCP delete_event failed: {e}")
            return False

    async def get_event(self, event_id: str, calendar_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get a specific calendar event using MCP.

        Args:
            event_id: Event ID to retrieve
            calendar_id: Calendar ID (defaults to primary)

        Returns:
            Event data or None if not found
        """
        if not await self._ensure_initialized():
            logger.warning("MCP not available, cannot get event")
            return None

        try:
            params = {
                "calendarId": calendar_id or "primary",
                "eventId": event_id
            }

            # Call MCP get tool
            if not self.workbench:
                raise RuntimeError("Workbench not initialized")
                
            result = await self.workbench.call_tool("calendar.events.get", params)
            
            # Extract event data
            event_data = None
            if result.result:
                for content in result.result:
                    if hasattr(content, 'content') and isinstance(content.content, str):
                        import json
                        try:
                            event_data = json.loads(content.content)
                            break
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse MCP get response: {content.content}")
                
            if event_data:
                logger.info(f"Retrieved event {event_id} via MCP")
            else:
                logger.warning(f"Event {event_id} not found via MCP")
                
            return event_data

        except Exception as e:
            logger.error(f"MCP get_event failed: {e}")
            return None

    async def get_available_tools(self) -> List[str]:
        """
        Get list of available MCP tool names.

        Returns:
            List of tool names
        """
        if not await self._ensure_initialized():
            return []

        tool_names = []
        for tool in self._tools:
            if hasattr(tool, "name"):
                tool_names.append(tool.name)
            elif isinstance(tool, dict) and "name" in tool:
                tool_names.append(tool["name"])
            else:
                tool_names.append(str(tool))

        return tool_names

    async def _ensure_initialized(self) -> bool:
        """Ensure MCP workbench is initialized."""
        if not self._initialized:
            return await self.initialize()
        return True

    async def cleanup(self) -> None:
        """Clean up MCP workbench resources."""
        if self.workbench:
            try:
                # The workbench should be closed properly if it was opened
                # Note: We don't call __aexit__ directly here as we use async with
                pass
            except Exception as e:
                logger.warning(f"Error cleaning up MCP workbench: {e}")

        self._initialized = False
        self.workbench = None


# Global MCP client instance
_mcp_client: Optional[CalendarMcpClient] = None


async def get_mcp_client() -> CalendarMcpClient:
    """
    Get the global MCP client instance.

    Returns:
        CalendarMcpClient instance
    """
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = CalendarMcpClient()
        await _mcp_client.initialize()
    return _mcp_client


async def cleanup_mcp_client() -> None:
    """Clean up the global MCP client."""
    global _mcp_client
    if _mcp_client:
        await _mcp_client.cleanup()
        _mcp_client = None
