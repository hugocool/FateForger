"""Notion MCP client helpers used by runtime agents."""

from __future__ import annotations

import os
from fateforger.tools.mcp_url_validation import NotionMcpEndpointResolver

_NOTION_ENDPOINT = NotionMcpEndpointResolver()


def get_notion_mcp_url() -> str:
    return _NOTION_ENDPOINT.resolve(os.environ)


def normalize_notion_mcp_url(raw_url: str) -> str:
    return _NOTION_ENDPOINT.validate(raw_url)


def validate_notion_mcp_url(value: str) -> str:
    return _NOTION_ENDPOINT.validate(value)


def probe_notion_mcp_endpoint(
    server_url: str, *, connect_timeout_s: float = 1.0
) -> tuple[bool, str | None]:
    return _NOTION_ENDPOINT.probe(server_url, connect_timeout_s=connect_timeout_s)


def get_notion_mcp_headers() -> dict[str, str] | None:
    token = (os.getenv("MCP_HTTP_AUTH_TOKEN") or "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


class NotionMcpClient:
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        from autogen_ext.tools.mcp import StreamableHttpServerParams

        self._server_url = validate_notion_mcp_url(server_url)
        self._timeout = timeout
        self._params = StreamableHttpServerParams(
            url=self._server_url,
            headers=get_notion_mcp_headers(),
            timeout=timeout,
        )

    async def get_tools(self) -> list:
        """Get Notion MCP tools for use by LLM agents."""
        from autogen_ext.tools.mcp import mcp_server_tools

        ok, reason = probe_notion_mcp_endpoint(
            self._server_url, connect_timeout_s=min(self._timeout, 1.5)
        )
        if not ok:
            raise RuntimeError(reason or "Notion MCP endpoint is unavailable.")
        tools = await mcp_server_tools(self._params)
        if not tools:
            raise RuntimeError("Notion MCP server returned no tools")
        return tools


__all__ = [
    "NotionMcpClient",
    "get_notion_mcp_headers",
    "get_notion_mcp_url",
    "normalize_notion_mcp_url",
    "validate_notion_mcp_url",
    "probe_notion_mcp_endpoint",
]
