from __future__ import annotations

from autogen_ext.tools.mcp import StreamableHttpServerParams

from fateforger.core.config import settings


def get_calendar_mcp_params(timeout: float = 10.0) -> StreamableHttpServerParams:
    """Build AutoGen MCP connection parameters for the Calendar MCP server."""
    return StreamableHttpServerParams(
        url=settings.mcp_calendar_server_url, timeout=timeout
    )


__all__ = ["get_calendar_mcp_params"]
