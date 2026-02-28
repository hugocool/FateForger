"""Notion MCP client helpers used by runtime agents."""

from __future__ import annotations

import logging
import os
import socket
from urllib.parse import urlparse

from pydantic import ValidationError
from yarl import URL

from fateforger.tools.mcp_url_validation import canonical_mcp_url, rewrite_mcp_host

logger = logging.getLogger(__name__)


def get_notion_mcp_url() -> str:
    configured = os.getenv("NOTION_MCP_URL") or os.getenv("WIZARD_NOTION_MCP_URL")
    if configured:
        normalized = normalize_notion_mcp_url(configured)
        parsed = URL(normalized)
        if parsed.host == "notion-mcp":
            ok, _reason = probe_notion_mcp_endpoint(normalized, connect_timeout_s=0.35)
            if not ok:
                fallback = rewrite_mcp_host(
                    normalized, "localhost", default_path="/mcp"
                )
                fallback_ok, _fallback_reason = probe_notion_mcp_endpoint(
                    fallback, connect_timeout_s=0.35
                )
                if fallback_ok:
                    logger.warning(
                        "Notion MCP URL '%s' is unreachable from host; using '%s'.",
                        normalized,
                        fallback,
                    )
                    return fallback
        return normalized

    port = os.getenv("MCP_HTTP_PORT", "3001").strip() or "3001"
    candidates = (
        canonical_mcp_url(f"http://notion-mcp:{port}", default_path="/mcp"),
        canonical_mcp_url(f"http://localhost:{port}", default_path="/mcp"),
        canonical_mcp_url(f"http://127.0.0.1:{port}", default_path="/mcp"),
    )
    for candidate in candidates:
        ok, _reason = probe_notion_mcp_endpoint(candidate, connect_timeout_s=0.35)
        if ok:
            return candidate
    return candidates[0]


def normalize_notion_mcp_url(raw_url: str) -> str:
    try:
        return canonical_mcp_url(raw_url.strip(), default_path="/mcp")
    except ValidationError:
        return raw_url.strip()


def validate_notion_mcp_url(value: str) -> str:
    """Validate Notion MCP endpoint URL and return it unchanged.

    Raises ValueError if the URL is missing required components.
    """
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Notion MCP URL is empty")
    if "://" not in raw:
        raise ValueError(
            "Notion MCP URL must include scheme (e.g. http://notion-mcp:3001/mcp)"
        )
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Notion MCP URL must use http or https scheme")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Notion MCP URL must include a host")
    if not parsed.path or parsed.path == "/":
        raise ValueError("Notion MCP URL must include explicit path (e.g. /mcp)")
    return raw


def probe_notion_mcp_endpoint(
    server_url: str, *, connect_timeout_s: float = 1.0
) -> tuple[bool, str | None]:
    try:
        validate_notion_mcp_url(server_url)
    except ValueError as exc:
        return False, str(exc)
    try:
        normalized = canonical_mcp_url(server_url, default_path="/mcp")
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid URL"}
        return False, f"Invalid Notion MCP URL ({first.get('msg')})."
    parsed = URL(normalized)
    host = parsed.host
    if not host:
        return False, "Notion MCP URL is missing a hostname."
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return (
            False,
            f"Notion MCP host '{host}' is not resolvable ({type(exc).__name__}).",
        )
    try:
        with socket.create_connection((host, port), timeout=connect_timeout_s):
            pass
    except OSError as exc:
        return (
            False,
            f"Notion MCP endpoint '{host}:{port}' is unreachable ({type(exc).__name__}).",
        )
    return True, None


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

        self._server_url = normalize_notion_mcp_url(server_url)
        self._timeout = timeout
        self._params = StreamableHttpServerParams(
            url=self._server_url,
            headers=get_notion_mcp_headers(),
            timeout=timeout,
        )

    async def get_tools(self) -> list:
        """Get Notion MCP tools for use by LLM agents."""
        try:
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
        except Exception as exc:
            logger.error("Failed to get Notion MCP tools", exc_info=True)
            raise RuntimeError("Failed to load Notion MCP tools") from exc


__all__ = [
    "NotionMcpClient",
    "get_notion_mcp_headers",
    "get_notion_mcp_url",
    "normalize_notion_mcp_url",
    "validate_notion_mcp_url",
    "probe_notion_mcp_endpoint",
]
