from __future__ import annotations

import pytest

import fateforger.tools.notion_mcp as notion_mcp
from fateforger.tools.notion_mcp import (
    NotionMcpClient,
    get_notion_mcp_url,
    normalize_notion_mcp_url,
)


def test_normalize_notion_mcp_url_adds_scheme_and_default_path() -> None:
    assert normalize_notion_mcp_url("localhost:3001") == "http://localhost:3001/mcp"
    assert (
        normalize_notion_mcp_url("http://localhost:3001")
        == "http://localhost:3001/mcp"
    )


def test_get_notion_mcp_url_normalizes_schemeless_env(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_MCP_URL", "localhost:3001")
    monkeypatch.setattr(
        notion_mcp,
        "probe_notion_mcp_endpoint",
        lambda *_args, **_kwargs: (True, None),
    )
    assert get_notion_mcp_url() == "http://localhost:3001/mcp"


def test_get_notion_mcp_url_uses_default_with_mcp_path(monkeypatch) -> None:
    monkeypatch.delenv("NOTION_MCP_URL", raising=False)
    monkeypatch.delenv("WIZARD_NOTION_MCP_URL", raising=False)
    monkeypatch.setenv("MCP_HTTP_PORT", "3001")
    monkeypatch.setattr(
        notion_mcp,
        "probe_notion_mcp_endpoint",
        lambda url, **_kwargs: (url == "http://localhost:3001/mcp", None),
    )
    assert get_notion_mcp_url() == "http://localhost:3001/mcp"


def test_get_notion_mcp_url_rewrites_container_host_when_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_MCP_URL", "notion-mcp:3001/mcp")

    def _probe(url: str, **_kwargs):
        if url == "http://notion-mcp:3001/mcp":
            return False, "dns"
        if url == "http://localhost:3001/mcp":
            return True, None
        return False, "unexpected"

    monkeypatch.setattr(notion_mcp, "probe_notion_mcp_endpoint", _probe)
    assert get_notion_mcp_url() == "http://localhost:3001/mcp"


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

    with pytest.raises(RuntimeError, match="Failed to load Notion MCP tools"):
        await client.get_tools()
