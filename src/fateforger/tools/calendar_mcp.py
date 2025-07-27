"""

Calendar Tools Configuration - MCP server parameters and tool loader for FateForger.

Provides a standardized function for loading Google Calendar MCP tools for AutoGen agents.
Environment variables should be loaded and managed externally.
"""

from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools


async def get_calendar_mcp_tools(server_url: str, timeout: float = 5.0):  # type: ignore
    """
    Return the list of MCP tools for Google Calendar using HTTP transport.

    Args:
        server_url (str): MCP server URL.
        timeout (float): Connection timeout in seconds.

    Returns:
        list: MCP tools for Google Calendar.
    """
    params = StreamableHttpServerParams(
        url=server_url,
        timeout=timeout,
    )
    return await mcp_server_tools(params)
