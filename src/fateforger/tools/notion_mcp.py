"""Notion MCP client helpers used by runtime agents."""

from __future__ import annotations

import os
from collections.abc import Mapping

from fateforger.tools.mcp_http_client import StreamableHttpMcpClient
from fateforger.tools.mcp_url_validation import NotionMcpEndpointResolver

_NOTION_ENDPOINT = NotionMcpEndpointResolver()


def get_notion_mcp_url() -> str:
    return _NOTION_ENDPOINT.resolve(os.environ)


def validate_notion_mcp_url(value: str) -> str:
    return _NOTION_ENDPOINT.validate(value)


def probe_notion_mcp_endpoint(
    server_url: str, *, connect_timeout_s: float = 1.0
) -> tuple[bool, str | None]:
    return _NOTION_ENDPOINT.probe(server_url, connect_timeout_s=connect_timeout_s)


def get_notion_mcp_headers(
    env: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    source = os.environ if env is None else env
    token = (source.get("MCP_HTTP_AUTH_TOKEN") or "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


class NotionMcpClient(StreamableHttpMcpClient):
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        super().__init__(
            resolver=_NOTION_ENDPOINT,
            server_url=server_url,
            timeout=timeout,
            connect_timeout_s=1.5,
            headers=get_notion_mcp_headers(),
        )

    def probe(self) -> tuple[bool, str | None]:
        return probe_notion_mcp_endpoint(
            self._server_url, connect_timeout_s=min(self._timeout, 1.5)
        )


__all__ = [
    "NotionMcpClient",
    "get_notion_mcp_headers",
    "get_notion_mcp_url",
    "validate_notion_mcp_url",
    "probe_notion_mcp_endpoint",
]
