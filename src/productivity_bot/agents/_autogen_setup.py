"""
AutoGen Setup Helper - Common configuration for agent communication.

This module provides shared setup utilities for AutoGen agents including
MCP tool configuration and common agent initialization patterns.
"""

import logging
from typing import Any, List

from autogen_ext.tools.mcp import McpWorkbench, StdioServerParams

from ..common import get_config, get_logger

logger = get_logger("autogen_setup")


async def build_mcp_tools() -> Any:
    """
    Build MCP tools for calendar operations.
    
    Returns:
        McpWorkbench instance with calendar tools
        
    Raises:
        Exception: If MCP server connection fails
    """
    try:
        config = get_config()
        
        # Configure MCP server parameters
        # Use Docker exec to connect to the calendar-mcp container
        params = StdioServerParams(
            command="docker",
            args=["exec", "calendar-mcp", "npm", "start"]
        )
        
        # Create workbench with MCP tools
        workbench = McpWorkbench(params)
        
        logger.info("MCP tools configured successfully")
        return workbench
        
    except Exception as e:
        logger.error(f"Failed to build MCP tools: {e}")
        # Return None to allow graceful degradation
        return None


def get_agent_config() -> dict:
    """
    Get common configuration for AutoGen agents.
    
    Returns:
        Dictionary with shared agent configuration
    """
    config = get_config()
    
    return {
        "model": "gpt-3.5-turbo-0125",  # Cheap model for routing
        "api_key": config.openai_api_key,
        "temperature": 0.1,  # Low temperature for consistent routing
        "max_tokens": 100,  # Small token limit for routing decisions
    }


def get_planner_agent_config() -> dict:
    """
    Get configuration specific to PlanningAgent.
    
    Returns:
        Dictionary with PlanningAgent configuration
    """
    config = get_config()
    
    return {
        "model": "gpt-4o-mini",  # Better model for planning operations
        "api_key": config.openai_api_key,
        "temperature": 0.2,
        "max_tokens": 500,
    }
