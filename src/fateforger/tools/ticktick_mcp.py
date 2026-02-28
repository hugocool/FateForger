"""
TickTick Tools Configuration - MCP server parameters and tool loader.
"""

from __future__ import annotations

import logging
import os
import socket
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


def get_ticktick_mcp_url() -> str:
    value = (
        os.getenv("TICKTICK_MCP_URL")
        or os.getenv("WIZARD_TICKTICK_MCP_URL")
        or "http://ticktick-mcp:8000/mcp"
    )
    return value.strip()


def validate_ticktick_mcp_url(value: str) -> str:
    """Validate TickTick MCP endpoint URL and return it unchanged."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("TickTick MCP URL is empty")
    if "://" not in raw:
        raise ValueError(
            "TickTick MCP URL must include scheme (e.g. http://ticktick-mcp:8000/mcp)"
        )
    candidate = raw
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("TickTick MCP URL must use http or https scheme")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("TickTick MCP URL must include a host")
    if not parsed.path or parsed.path == "/":
        raise ValueError("TickTick MCP URL must include explicit path (e.g. /mcp)")
    return raw


def probe_ticktick_mcp_endpoint(
    server_url: str,
    *,
    connect_timeout_s: float = 1.5,
) -> tuple[bool, str]:
    """Cheap connectivity probe used before opening an MCP workbench."""
    try:
        parsed = urlparse(validate_ticktick_mcp_url(server_url))
    except ValueError as exc:
        return False, str(exc)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False, f"Invalid TickTick MCP URL: '{server_url}'"
    try:
        with socket.create_connection((host, port), timeout=connect_timeout_s):
            return True, ""
    except OSError as exc:
        return False, f"TickTick MCP endpoint '{server_url}' is unreachable: {exc}"


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


__all__ = [
    "TickTickMcpClient",
    "get_ticktick_mcp_url",
    "validate_ticktick_mcp_url",
    "probe_ticktick_mcp_endpoint",
]
