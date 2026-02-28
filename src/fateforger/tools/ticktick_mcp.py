"""
TickTick Tools Configuration - MCP server parameters and tool loader.
"""

from __future__ import annotations

import os
from fateforger.tools.mcp_http_client import StreamableHttpMcpClient
from fateforger.tools.mcp_url_validation import TickTickMcpEndpointResolver


_TICKTICK_ENDPOINT = TickTickMcpEndpointResolver()


def get_ticktick_mcp_url() -> str:
    return _TICKTICK_ENDPOINT.resolve(os.environ)


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


class TickTickMcpClient(StreamableHttpMcpClient):
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        super().__init__(
            resolver=_TICKTICK_ENDPOINT,
            server_url=server_url,
            timeout=timeout,
            connect_timeout_s=1.5,
        )

    def probe(self) -> tuple[bool, str]:
        ok, reason = probe_ticktick_mcp_endpoint(
            self._server_url, connect_timeout_s=min(self._timeout, 1.5)
        )
        return ok, reason or ""


__all__ = [
    "TickTickMcpClient",
    "get_ticktick_mcp_url",
    "validate_ticktick_mcp_url",
    "probe_ticktick_mcp_endpoint",
]
