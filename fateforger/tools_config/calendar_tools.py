"""
Calendar Tools Configuration - MCP server parameters and tool loaders for FateForger.

Provides standardized configuration for connecting to Google Calendar MCP servers
and loading calendar tools for AutoGen agents.
"""

import os

from autogen_ext.tools.mcp import StreamableHttpServerParams


def get_calendar_mcp_params(
    server_url: str | None = None, timeout: float = 10.0
) -> StreamableHttpServerParams:
    """
    Get configured MCP parameters for Google Calendar server.

    Args:
        server_url: Override for MCP server URL (defaults to env var or localhost:3000)
        timeout: Connection timeout in seconds

    Returns:
        Configured StreamableHttpServerParams for HTTP transport
    """
    if server_url is None:
        server_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")

    return StreamableHttpServerParams(
        url=server_url,
        timeout=timeout,
    )
