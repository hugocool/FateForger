from __future__ import annotations

import pytest

import fateforger.tools.notion_mcp as notion_mcp
from fateforger.tools.notion_mcp import (
    NotionMcpClient,
    get_notion_mcp_url,
    normalize_notion_mcp_url,
)


def test_normalize_notion_mcp_url_validates_without_normalizing() -> None:
    configured = "http://localhost:3001/mcp?transport=sse"
    assert normalize_notion_mcp_url(configured) == configured


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
