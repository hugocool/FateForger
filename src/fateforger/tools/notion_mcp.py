"""Notion MCP client helpers used by runtime agents."""

from __future__ import annotations

import logging
import os
import socket
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


def get_notion_mcp_url() -> str:
    value = os.getenv("NOTION_MCP_URL") or os.getenv("WIZARD_NOTION_MCP_URL")
    if value:
        return value.strip()
    port = os.getenv("MCP_HTTP_PORT", "3001").strip() or "3001"
    return f"http://notion-mcp:{port}/mcp"


def get_notion_mcp_headers() -> dict[str, str] | None:
    token = (os.getenv("MCP_HTTP_AUTH_TOKEN") or "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def validate_notion_mcp_url(value: str) -> str:
    """Validate Notion MCP endpoint URL and return it unchanged."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Notion MCP URL is empty")
    if "://" not in raw:
        raise ValueError(
            "Notion MCP URL must include scheme (e.g. http://notion-mcp:3001/mcp)"
        )
    candidate = raw
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Notion MCP URL must use http or https scheme")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Notion MCP URL must include a host")
    if not parsed.path or parsed.path == "/":
        raise ValueError("Notion MCP URL must include explicit path (e.g. /mcp)")
    return raw


def probe_notion_mcp_endpoint(
    server_url: str,
    *,
    connect_timeout_s: float = 1.5,
) -> tuple[bool, str]:
    """Cheap TCP connectivity probe before creating the MCP workbench."""
    try:
        parsed = urlparse(validate_notion_mcp_url(server_url))
    except ValueError as exc:
        return False, str(exc)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False, f"Invalid Notion MCP URL: '{server_url}'"
    try:
        with socket.create_connection((host, port), timeout=connect_timeout_s):
            return True, ""
    except OSError as exc:
        return False, f"Notion MCP endpoint '{server_url}' is unreachable: {exc}"


class NotionMcpClient:
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        try:
            from autogen_ext.tools.mcp import StreamableHttpServerParams
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "autogen_ext tools are required for Notion MCP access"
            ) from e

        self._params = StreamableHttpServerParams(
            url=server_url,
            headers=get_notion_mcp_headers(),
            timeout=timeout,
        )

    async def get_tools(self) -> list:
        """Get Notion MCP tools for use by LLM agents."""
        try:
            from autogen_ext.tools.mcp import mcp_server_tools

            tools = await mcp_server_tools(self._params)
            if not tools:
                raise RuntimeError("Notion MCP server returned no tools")
            return tools
        except Exception as exc:
            logger.error("Failed to get Notion MCP tools", exc_info=True)
            raise RuntimeError("Failed to load Notion MCP tools") from exc


__all__ = [
    "NotionMcpClient",
    "get_notion_mcp_headers",
    "get_notion_mcp_url",
    "validate_notion_mcp_url",
    "probe_notion_mcp_endpoint",
]
