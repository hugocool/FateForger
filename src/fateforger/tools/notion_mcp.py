"""Notion MCP client helpers used by runtime agents."""

from __future__ import annotations

import logging
import os


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


__all__ = ["NotionMcpClient", "get_notion_mcp_headers", "get_notion_mcp_url"]
