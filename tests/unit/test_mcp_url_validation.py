from __future__ import annotations

import pytest

from fateforger.tools.notion_mcp import (
    probe_notion_mcp_endpoint,
    validate_notion_mcp_url,
)
from fateforger.tools.mcp_url_validation import (
    NotionMcpEndpointResolver,
    TickTickMcpEndpointResolver,
)
from fateforger.tools.ticktick_mcp import (
    normalize_ticktick_mcp_url,
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


def test_normalize_ticktick_mcp_url_is_validate_only() -> None:
    configured = "http://ticktick-mcp:8000/mcp?transport=sse"
    assert normalize_ticktick_mcp_url(configured) == configured


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
