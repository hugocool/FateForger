"""URL validation, endpoint resolution and host rewriting for the MCP clients.

Notion and TickTick share one validator shape, so both live here rather than in
a per-server file.
"""

from __future__ import annotations

import pytest

import fateforger.tools.notion_mcp as notion_mcp
from fateforger.tools.notion_mcp import (
    NotionMcpClient,
    get_notion_mcp_url,
    normalize_notion_mcp_url,
    probe_notion_mcp_endpoint,
    validate_notion_mcp_url,
)
from fateforger.tools.mcp_url_validation import (
    NotionMcpEndpointResolver,
    TickTickMcpEndpointResolver,
    rewrite_mcp_host,
)
from fateforger.tools.ticktick_mcp import (
    probe_ticktick_mcp_endpoint,
    validate_ticktick_mcp_url,
)


@pytest.mark.parametrize(
    ("value", "expected_error"),
    [
        ("", "empty"),
        ("ticktick-mcp:8000/mcp", "must include scheme"),
        ("ftp://ticktick-mcp:8000/mcp", "must use http or https"),
        ("http:///mcp", "must include a host"),
        ("http://ticktick-mcp:8000/", "must include explicit path"),
    ],
)
def test_validate_ticktick_mcp_url_fails_loudly(
    value: str, expected_error: str
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        validate_ticktick_mcp_url(value)


def test_validate_ticktick_mcp_url_does_not_normalize() -> None:
    configured = "http://ticktick-mcp:8000/mcp?transport=sse"
    assert validate_ticktick_mcp_url(configured) == configured


def test_probe_ticktick_mcp_endpoint_reports_validation_error() -> None:
    ok, reason = probe_ticktick_mcp_endpoint("ticktick-mcp:8000/mcp")
    assert ok is False
    assert "must include scheme" in reason


@pytest.mark.parametrize(
    ("value", "expected_error"),
    [
        ("", "empty"),
        ("notion-mcp:3001/mcp", "must include scheme"),
        ("ftp://notion-mcp:3001/mcp", "must use http or https"),
        ("http:///mcp", "must include a host"),
        ("http://notion-mcp:3001/", "must include explicit path"),
    ],
)
def test_validate_notion_mcp_url_fails_loudly(
    value: str, expected_error: str
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        validate_notion_mcp_url(value)


def test_validate_notion_mcp_url_does_not_normalize() -> None:
    configured = "http://notion-mcp:3001/mcp?transport=sse"
    assert validate_notion_mcp_url(configured) == configured


def test_normalize_notion_mcp_url_matches_validator() -> None:
    configured = "http://notion-mcp:3001/mcp?transport=sse"
    assert normalize_notion_mcp_url(configured) == configured


def test_probe_notion_mcp_endpoint_reports_validation_error() -> None:
    ok, reason = probe_notion_mcp_endpoint("notion-mcp:3001/mcp")
    assert ok is False
    assert "must include scheme" in reason


def test_notion_endpoint_resolver_uses_mcp_http_port_default() -> None:
    resolver = NotionMcpEndpointResolver()
    resolved = resolver.resolve({"MCP_HTTP_PORT": "3100"})
    assert resolved == "http://localhost:3100/mcp"


def test_ticktick_endpoint_resolver_prefers_explicit_env() -> None:
    resolver = TickTickMcpEndpointResolver()
    resolved = resolver.resolve({"TICKTICK_MCP_URL": "http://host:8000/mcp"})
    assert resolved == "http://host:8000/mcp"


def test_rewrite_mcp_host_preserves_scheme_port_and_query() -> None:
    rewritten = rewrite_mcp_host(
        "https://notion-mcp:3443/mcp?transport=sse",
        "localhost",
    )
    assert rewritten == "https://localhost:3443/mcp?transport=sse"


def test_rewrite_mcp_host_adds_default_path_when_missing() -> None:
    rewritten = rewrite_mcp_host(
        "http://notion-mcp:3001",
        "host.docker.internal",
        default_path="/mcp",
    )
    assert rewritten == "http://host.docker.internal:3001/mcp"


# ── get_notion_mcp_url(): environment resolution ────────────────────────────


def test_get_notion_mcp_url_rejects_schemeless_env(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_MCP_URL", "localhost:3001")
    with pytest.raises(ValueError, match="must include scheme"):
        get_notion_mcp_url()


def test_get_notion_mcp_url_uses_explicit_default_with_port(monkeypatch) -> None:
    monkeypatch.delenv("NOTION_MCP_URL", raising=False)
    monkeypatch.delenv("WIZARD_NOTION_MCP_URL", raising=False)
    monkeypatch.setenv("MCP_HTTP_PORT", "3001")
    assert get_notion_mcp_url() == "http://localhost:3001/mcp"


def test_get_notion_mcp_url_accepts_explicit_url_without_rewrite(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_MCP_URL", "http://notion-mcp:3001/mcp")
    assert get_notion_mcp_url() == "http://notion-mcp:3001/mcp"


@pytest.mark.asyncio
async def test_notion_client_get_tools_raises_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("autogen_ext.tools.mcp")
    monkeypatch.setattr(
        notion_mcp,
        "probe_notion_mcp_endpoint",
        lambda *_args, **_kwargs: (False, "endpoint unavailable"),
    )
    client = NotionMcpClient.__new__(NotionMcpClient)
    client._params = object()
    client._server_url = "http://example.invalid/mcp"
    client._timeout = 1.0

    with pytest.raises(RuntimeError, match="endpoint unavailable"):
        await client.get_tools()
