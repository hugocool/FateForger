"""
MCP (Model Context Protocol) client for connecting to calendar services.

This module provides integration with the Google Calendar MCP server running
in the nspady/google-calendar-mcp container.
"""

import logging
from typing import List, Any

from autogen_ext.tools.mcp import McpWorkbench, SseServerParams
from autogen_ext.models.openai import OpenAIChatCompletionClient

from ..common import get_config, get_logger

logger = get_logger("mcp_client")

# 1. Point to your running MCP container 
#    (nspady/google-calendar-mcp exposes HTTP JSON-RPC at /mcp)
SERVER_URL = "http://mcp:4000/mcp"

# 2. Initialize the LLM client
def get_llm_client():
    """Get configured OpenAI chat completion client."""
    config = get_config()
    return OpenAIChatCompletionClient(
        model="gpt-4",
        api_key=config.openai_api_key
    )

# 3. Create a Workbench that discovers tools from the MCP server
def get_mcp_workbench():
    """Get configured MCP workbench for calendar operations."""
    mcp_params = SseServerParams(url=SERVER_URL, timeout=30)
    return McpWorkbench(server_params=mcp_params)

async def get_calendar_tools() -> List[Any]:
    """
    Returns a list of tool adapters for calendar operations.
    
    This function connects to the MCP server and discovers all available
    calendar tools (like create_event, list_events, etc.)
    
    Returns:
        List of tool adapters that can be used with AutoGen agents
    """
    workbench = get_mcp_workbench()
    try:
        # Note: The exact API may need adjustment based on autogen-ext version
        # This is a placeholder that will be refined during testing
        tools = []
        logger.info(f"Discovered {len(tools)} calendar tools from MCP server")
        return tools
    except Exception as e:
        logger.error(f"Failed to get calendar tools from MCP server: {e}")
        return []

async def test_mcp_connection() -> bool:
    """
    Test connection to the MCP server.
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        tools = await get_calendar_tools()
        return len(tools) > 0
    except Exception as e:
        logger.error(f"MCP connection test failed: {e}")
        return False
