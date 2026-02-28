"""Shared composable streamable-HTTP MCP client primitives."""

from __future__ import annotations

from collections.abc import Mapping

from fateforger.tools.mcp_url_validation import McpEndpointResolver


class StreamableHttpMcpClient:
    """Reusable MCP client with strict endpoint validation and reachability checks."""

    def __init__(
        self,
        *,
        resolver: McpEndpointResolver,
        server_url: str,
        timeout: float = 5.0,
        connect_timeout_s: float = 1.5,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        from autogen_ext.tools.mcp import StreamableHttpServerParams

        self._resolver = resolver
        self._server_url = resolver.validate(server_url)
        self._timeout = timeout
        self._connect_timeout_s = connect_timeout_s
        self._params = StreamableHttpServerParams(
            url=self._server_url,
            headers=dict(headers) if headers else None,
            timeout=timeout,
        )

    def probe(self) -> tuple[bool, str | None]:
        connect_timeout_s = min(self._timeout, self._connect_timeout_s)
        return self._resolver.probe(
            self._server_url, connect_timeout_s=connect_timeout_s
        )

    def _endpoint_name(self) -> str:
        resolver = getattr(self, "_resolver", None)
        policy = getattr(resolver, "policy", None)
        name = getattr(policy, "name", None)
        return name if isinstance(name, str) and name.strip() else "MCP"

    async def get_tools(self) -> list:
        """Load MCP tools from the configured endpoint."""
        from autogen_ext.tools.mcp import mcp_server_tools

        ok, reason = self.probe()
        if not ok:
            raise RuntimeError(reason or f"{self._endpoint_name()} endpoint is unavailable.")
        tools = await mcp_server_tools(self._params)
        if not tools:
            raise RuntimeError(f"{self._endpoint_name()} server returned no tools")
        return tools


__all__ = ["StreamableHttpMcpClient"]
