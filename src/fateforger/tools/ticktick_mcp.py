"""
TickTick Tools Configuration - MCP server parameters and tool loader.
"""

from __future__ import annotations

import logging
import os
import socket

import httpx
from pydantic import ValidationError
from yarl import URL

from fateforger.tools.mcp_url_validation import canonical_mcp_url


logger = logging.getLogger(__name__)


def _ticktick_host_port(default: int = 8002) -> int:
    raw = (os.getenv("TICKTICK_MCP_HOST_PORT") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if 1 <= value <= 65535 else default


def _localhost_fallback_urls(configured: str) -> tuple[str, ...]:
    """Candidate localhost URLs for host-run processes."""
    parsed = URL(configured)
    host_port = _ticktick_host_port()
    candidates: list[str] = []
    for host in ("localhost", "127.0.0.1"):
        same_port = str(parsed.with_host(host))
        candidates.append(same_port)
        parsed_port = parsed.port
        if parsed_port == 8000 and host_port != 8000:
            candidates.append(str(parsed.with_host(host).with_port(host_port)))
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return tuple(deduped)


def ticktick_localhost_fallback_urls(server_url: str) -> tuple[str, ...]:
    """Public helper for host-run fallback candidates."""
    try:
        normalized = canonical_mcp_url(server_url, default_path="/mcp")
    except ValidationError:
        return ()
    return _localhost_fallback_urls(normalized)


def normalize_ticktick_mcp_url(raw_url: str) -> str:
    """Return a canonical TickTick MCP URL with scheme and path."""
    try:
        return canonical_mcp_url(raw_url, default_path="/mcp")
    except ValidationError:
        return (raw_url or "").strip()


def get_ticktick_mcp_url() -> str:
    configured = normalize_ticktick_mcp_url(
        os.getenv("TICKTICK_MCP_URL") or os.getenv("WIZARD_TICKTICK_MCP_URL") or ""
    ).strip()
    if configured:
        parsed = URL(configured)
        # Common host-run misconfiguration: docker service DNS name from a host process.
        # Auto-correct to localhost with same port/path so MCP remains reachable.
        if parsed.host == "ticktick-mcp":
            ok, _reason = probe_ticktick_mcp_endpoint(configured, connect_timeout_s=0.35)
            if not ok:
                for fallback in ticktick_localhost_fallback_urls(configured):
                    fallback_ok, _fallback_reason = probe_ticktick_mcp_endpoint(
                        fallback, connect_timeout_s=0.35
                    )
                    if fallback_ok:
                        logger.warning(
                            "TickTick MCP URL '%s' is unreachable from host; using '%s'.",
                            configured,
                            fallback,
                        )
                        return fallback
        return configured

    # Default to docker service name in containerized runs, but fall back to localhost
    # when developing directly on host where `ticktick-mcp` DNS is unavailable.
    host_port = _ticktick_host_port()
    candidates = (
        "http://ticktick-mcp:8000/mcp",
        f"http://localhost:{host_port}/mcp",
        f"http://127.0.0.1:{host_port}/mcp",
        "http://localhost:8000/mcp",
        "http://127.0.0.1:8000/mcp",
    )
    for candidate in candidates:
        ok, _reason = probe_ticktick_mcp_endpoint(candidate, connect_timeout_s=0.35)
        if ok:
            return candidate
    return candidates[0]


def probe_ticktick_mcp_endpoint(
    server_url: str, *, connect_timeout_s: float = 1.0
) -> tuple[bool, str | None]:
    """Return endpoint reachability for a TickTick MCP URL."""
    try:
        normalized = canonical_mcp_url(server_url, default_path="/mcp")
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid URL"}
        return False, f"Invalid TickTick MCP URL ({first.get('msg')})."

    parsed = URL(normalized)
    host = parsed.host
    if not host:
        return False, "TickTick MCP URL is missing a hostname."
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return (
            False,
            f"TickTick MCP host '{host}' is not resolvable ({type(exc).__name__}).",
        )

    try:
        with socket.create_connection((host, port), timeout=connect_timeout_s):
            pass
    except OSError as exc:
        return (
            False,
            f"TickTick MCP endpoint '{host}:{port}' is unreachable ({type(exc).__name__}).",
        )

    timeout = httpx.Timeout(
        timeout=connect_timeout_s,
        connect=connect_timeout_s,
        read=connect_timeout_s,
        write=connect_timeout_s,
    )
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.post(
                normalized,
                json={
                    "jsonrpc": "2.0",
                    "id": "probe-tools-list",
                    "method": "tools/list",
                    "params": {},
                },
                headers={"Accept": "application/json, text/event-stream"},
            )
    except httpx.HTTPError as exc:
        return False, f"TickTick MCP HTTP probe failed ({type(exc).__name__})."

    status = response.status_code
    if status in {404, 405, 501}:
        return (
            False,
            f"TickTick MCP endpoint '{normalized}' does not support MCP POST (HTTP {status}).",
        )
    if status >= 500:
        return (
            False,
            f"TickTick MCP endpoint '{normalized}' returned server error HTTP {status}.",
        )

    return True, None


class TickTickMcpClient:
    def __init__(self, *, server_url: str, timeout: float = 5.0) -> None:
        try:
            from autogen_ext.tools.mcp import StreamableHttpServerParams
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                "autogen_ext tools are required for TickTick MCP access"
            ) from e
        self._server_url = server_url.strip()
        self._timeout = timeout
        self._params = StreamableHttpServerParams(url=self._server_url, timeout=timeout)

    async def get_tools(self) -> list:
        """Get TickTick MCP tools for use by LLM agents."""
        try:
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
        except Exception as exc:
            logger.error("Failed to get TickTick MCP tools", exc_info=True)
            raise RuntimeError("Failed to load TickTick MCP tools") from exc


__all__ = [
    "TickTickMcpClient",
    "get_ticktick_mcp_url",
    "normalize_ticktick_mcp_url",
    "probe_ticktick_mcp_endpoint",
    "ticktick_localhost_fallback_urls",
]
