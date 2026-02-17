"""
TickTick Tools Configuration - MCP server parameters and tool loader.
"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)


def get_ticktick_mcp_url() -> str:
    value = (
        os.getenv("TICKTICK_MCP_URL")
        or os.getenv("WIZARD_TICKTICK_MCP_URL")
        or "http://ticktick-mcp:8000/mcp"
    )
    return value.strip()


class TickTickMcpClient:
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        try:
            from autogen_ext.tools.mcp import StreamableHttpServerParams
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "autogen_ext tools are required for TickTick MCP access"
            ) from e
        self._params = StreamableHttpServerParams(url=server_url, timeout=timeout)

    async def get_tools(self) -> list:
        """Get TickTick MCP tools for use by LLM agents."""
        try:
            from autogen_ext.tools.mcp import mcp_server_tools

            tools = await mcp_server_tools(self._params)
            if not tools:
                raise RuntimeError("TickTick MCP server returned no tools")
            return tools
        except Exception as exc:
            logger.error("Failed to get TickTick MCP tools", exc_info=True)
            raise RuntimeError("Failed to load TickTick MCP tools") from exc


__all__ = ["TickTickMcpClient", "get_ticktick_mcp_url"]
