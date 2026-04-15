"""Google Calendar MCP client helpers used by runtime agents."""

from __future__ import annotations

import os

from fateforger.tools.mcp_http_client import StreamableHttpMcpClient
from fateforger.tools.mcp_url_validation import CalendarMcpEndpointResolver

_CALENDAR_ENDPOINT = CalendarMcpEndpointResolver()


def get_calendar_mcp_url() -> str:
    return _CALENDAR_ENDPOINT.resolve(os.environ)


def validate_calendar_mcp_url(value: str) -> str:
    return _CALENDAR_ENDPOINT.validate(value)


def probe_calendar_mcp_endpoint(
    server_url: str, *, connect_timeout_s: float = 1.5
) -> tuple[bool, str | None]:
    return _CALENDAR_ENDPOINT.probe(server_url, connect_timeout_s=connect_timeout_s)


class CalendarMcpClient(StreamableHttpMcpClient):
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        super().__init__(
            resolver=_CALENDAR_ENDPOINT,
            server_url=server_url,
            timeout=timeout,
            connect_timeout_s=1.5,
        )

    def probe(self) -> tuple[bool, str | None]:
        return probe_calendar_mcp_endpoint(
            self._server_url, connect_timeout_s=min(self._timeout, 1.5)
        )


async def get_calendar_mcp_tools(server_url: str, timeout: float = 5.0) -> list:
    """Return the list of MCP tools for Google Calendar using HTTP transport.

    Kept for backward compatibility. Prefer ``CalendarMcpClient.get_tools()``.
    """
    client = CalendarMcpClient(server_url=server_url, timeout=timeout)
    return await client.get_tools()


__all__ = [
    "CalendarMcpClient",
    "get_calendar_mcp_tools",
    "get_calendar_mcp_url",
    "probe_calendar_mcp_endpoint",
    "validate_calendar_mcp_url",
]
