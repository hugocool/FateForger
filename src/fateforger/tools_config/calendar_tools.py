from __future__ import annotations

import os

from autogen_ext.tools.mcp import StreamableHttpServerParams


def get_calendar_mcp_params(timeout: float = 10.0) -> StreamableHttpServerParams:
    url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
    return StreamableHttpServerParams(url=url, timeout=timeout)


__all__ = ["get_calendar_mcp_params"]
