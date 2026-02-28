"""Composable MCP endpoint validation/resolution helpers."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import ParseResult, urlparse, urlunparse


def _normalize_default_path(default_path: str) -> str:
    value = (default_path or "").strip() or "/mcp"
    return value if value.startswith("/") else f"/{value}"


def _validate_url(
    value: str,
    *,
    label: str,
    require_explicit_path: bool = True,
) -> tuple[str, ParseResult]:
    raw = (value or "").strip()
    if not raw:
        raise ValueError(f"{label} is empty")
    if "://" not in raw:
        raise ValueError(f"{label} must include scheme (e.g. http://host:port/mcp)")

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{label} must use http or https scheme")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError(f"{label} must include a host")
    if require_explicit_path and (not parsed.path or parsed.path == "/"):
        raise ValueError(f"{label} must include explicit path (e.g. /mcp)")
    return raw, parsed


@dataclass(frozen=True)
class McpEndpointPolicy:
    name: str
    env_vars: tuple[str, ...]
    default_url: str
    default_path: str = "/mcp"


class McpEndpointResolver:
    """Base endpoint resolver with strict validation and deterministic probing."""

    def __init__(self, policy: McpEndpointPolicy) -> None:
        self._policy = policy

    def _default_url(self, env: Mapping[str, str]) -> str:
        return self._policy.default_url

    def resolve(self, env: Mapping[str, str] | None = None) -> str:
        source = dict(os.environ) if env is None else env
        for key in self._policy.env_vars:
            configured = (source.get(key) or "").strip()
            if configured:
                return self.validate(configured)
        return self.validate(self._default_url(source))

    def validate(self, value: str) -> str:
        raw, _parsed = _validate_url(value, label=f"{self._policy.name} URL")
        return raw

    def probe(
        self, server_url: str, *, connect_timeout_s: float = 1.5
    ) -> tuple[bool, str | None]:
        try:
            validated, parsed = _validate_url(
                server_url, label=f"{self._policy.name} URL"
            )
        except ValueError as exc:
            return False, str(exc)

        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False, f"{self._policy.name} URL must include a host"
        try:
            socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            return (
                False,
                f"{self._policy.name} host '{host}' is not resolvable ({type(exc).__name__}).",
            )
        try:
            with socket.create_connection((host, port), timeout=connect_timeout_s):
                pass
        except OSError as exc:
            return (
                False,
                f"{self._policy.name} endpoint '{validated}' is unreachable ({type(exc).__name__}).",
            )
        return True, None


class NotionMcpEndpointResolver(McpEndpointResolver):
    """Notion endpoint policy with local-dev default based on MCP_HTTP_PORT."""

    def __init__(self) -> None:
        super().__init__(
            McpEndpointPolicy(
                name="Notion MCP",
                env_vars=("NOTION_MCP_URL", "WIZARD_NOTION_MCP_URL"),
                default_url="http://localhost:3001/mcp",
            )
        )

    def _default_url(self, env: Mapping[str, str]) -> str:
        port = (env.get("MCP_HTTP_PORT") or "3001").strip() or "3001"
        return f"http://localhost:{port}/mcp"


class TickTickMcpEndpointResolver(McpEndpointResolver):
    """TickTick endpoint policy with strict non-normalizing validation."""

    def __init__(self) -> None:
        super().__init__(
            McpEndpointPolicy(
                name="TickTick MCP",
                env_vars=("TICKTICK_MCP_URL", "WIZARD_TICKTICK_MCP_URL"),
                default_url="http://ticktick-mcp:8000/mcp",
            )
        )


def canonical_mcp_url(raw_url: str, *, default_path: str = "/mcp") -> str:
    """Return URL with explicit path; useful for deterministic defaulting."""
    raw, parsed = _validate_url(raw_url, label="MCP URL", require_explicit_path=False)
    _ = raw
    if parsed.path and parsed.path != "/":
        return raw
    normalized_path = _normalize_default_path(default_path)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def rewrite_mcp_host(raw_url: str, host: str, *, default_path: str = "/mcp") -> str:
    """Rewrite hostname while keeping scheme/port/path/query."""
    _raw, parsed = _validate_url(
        raw_url, label="MCP URL", require_explicit_path=False
    )
    normalized_path = (
        parsed.path
        if parsed.path and parsed.path != "/"
        else _normalize_default_path(default_path)
    )
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        auth = f"{auth}@"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{auth}{host}{port}"
    return urlunparse(
        (
            parsed.scheme,
            netloc,
            normalized_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


__all__ = [
    "McpEndpointPolicy",
    "McpEndpointResolver",
    "NotionMcpEndpointResolver",
    "TickTickMcpEndpointResolver",
    "canonical_mcp_url",
    "rewrite_mcp_host",
]
