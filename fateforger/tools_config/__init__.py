"""
FateForger Tools Config - MCP server configuration and tool loading utilities.

This module provides standardized configuration for connecting to external
MCP servers and loading tools for AutoGen agents.
"""

from .calendar_tools import get_calendar_mcp_params

__all__ = [
    "get_calendar_mcp_params",
]
