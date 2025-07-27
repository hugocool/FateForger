"""
MCP Workbench wrapper for daily planning checks.

This module provides a simplified interface to MCP workbench functionality
specifically for the daily planner checker.
"""

import logging
from typing import Any, Dict, List, Optional

from ..common import get_logger
from ..mcp_integration import CalendarMcpClient

logger = get_logger("mcp_workbench")


class McpWorkbench:
    """
    Simplified MCP Workbench wrapper for daily planning operations.

    This class provides a simplified interface to calendar operations
    needed by the daily planner checker.
    """

    def __init__(self):
        """Initialize the MCP workbench wrapper."""
        self._calendar_client = CalendarMcpClient()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the MCP workbench."""
        try:
            success = await self._calendar_client.initialize()
            if success:
                self._initialized = True
                logger.info("MCP Workbench initialized successfully")
            else:
                logger.error("Failed to initialize MCP Workbench")
                raise RuntimeError("MCP Workbench initialization failed")

        except Exception as e:
            logger.error(f"Error initializing MCP Workbench: {e}")
            raise

    async def search_events(
        self, search_params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Search for events using MCP calendar tools.

        Args:
            search_params: Dictionary with search parameters including:
                - start_time: ISO format start time
                - end_time: ISO format end time
                - query: Text query for event search

        Returns:
            List of event dictionaries
        """
        if not self._initialized:
            logger.error("MCP Workbench not initialized")
            return []

        try:
            # Use the calendar MCP client to search for events
            events = await self._calendar_client.search_events(
                start_time=search_params.get("start_time"),
                end_time=search_params.get("end_time"),
                query=search_params.get("query", ""),
            )

            return events

        except Exception as e:
            logger.error(f"Error searching events via MCP: {e}")
            return []

    async def cleanup(self) -> None:
        """Clean up MCP workbench resources."""
        try:
            if self._calendar_client:
                await self._calendar_client.cleanup()
            self._initialized = False
            logger.info("MCP Workbench cleaned up")

        except Exception as e:
            logger.error(f"Error cleaning up MCP Workbench: {e}")
