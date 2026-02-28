"""
TickTick Tools Configuration - MCP server parameters and tool loader.
"""

from __future__ import annotations

import os
from fateforger.tools.mcp_url_validation import TickTickMcpEndpointResolver


_TICKTICK_ENDPOINT = TickTickMcpEndpointResolver()


def get_ticktick_mcp_url() -> str:
    return _TICKTICK_ENDPOINT.resolve(os.environ)


def normalize_ticktick_mcp_url(raw_url: str) -> str:
    return _TICKTICK_ENDPOINT.validate(raw_url)


def validate_ticktick_mcp_url(value: str) -> str:
    return _TICKTICK_ENDPOINT.validate(value)


def probe_ticktick_mcp_endpoint(
    server_url: str,
    *,
    connect_timeout_s: float = 1.5,
) -> tuple[bool, str]:
    """Cheap connectivity probe used before opening an MCP workbench."""
    ok, reason = _TICKTICK_ENDPOINT.probe(
        server_url, connect_timeout_s=connect_timeout_s
    )
    return ok, reason or ""


class TickTickMcpClient:
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        from autogen_ext.tools.mcp import StreamableHttpServerParams

        self._server_url = validate_ticktick_mcp_url(server_url)
        self._timeout = timeout
        self._params = StreamableHttpServerParams(
            url=self._server_url, timeout=timeout
        )

    async def get_tools(self) -> list:
        """Get TickTick MCP tools for use by LLM agents."""
        from autogen_ext.tools.mcp import mcp_server_tools

        ok, reason = probe_ticktick_mcp_endpoint(
            self._server_url, connect_timeout_s=min(self._timeout, 1.5)
        )
        if not ok:
            raise RuntimeError(reason or "TickTick MCP endpoint is unavailable.")
        tools = await mcp_server_tools(self._params)
        if not tools:
            raise RuntimeError("TickTick MCP server returned no tools")
        return tools


__all__ = [
    "TickTickMcpClient",
    "get_ticktick_mcp_url",
    "normalize_ticktick_mcp_url",
    "validate_ticktick_mcp_url",
    "probe_ticktick_mcp_endpoint",
]
