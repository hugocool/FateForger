from __future__ import annotations

import pytest

import fateforger.tools.ticktick_mcp as ticktick_mcp
from fateforger.tools.ticktick_mcp import (
    TickTickMcpClient,
    get_ticktick_mcp_url,
    normalize_ticktick_mcp_url,
    probe_ticktick_mcp_endpoint,
)


pytest.importorskip("autogen_ext.tools.mcp")


def test_normalize_ticktick_mcp_url_adds_scheme_and_default_path() -> None:
    assert normalize_ticktick_mcp_url("localhost:8000") == "http://localhost:8000/mcp"
    assert (
        normalize_ticktick_mcp_url("http://localhost:8000")
        == "http://localhost:8000/mcp"
    )
    assert (
        normalize_ticktick_mcp_url("http://localhost:8000/custom")
        == "http://localhost:8000/custom"
    )


def test_get_ticktick_mcp_url_rewrites_schemeless_ticktick_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TICKTICK_MCP_URL", "ticktick-mcp:8000/mcp")
    monkeypatch.delenv("TICKTICK_MCP_HOST_PORT", raising=False)

    def _probe(url: str, *, connect_timeout_s: float = 1.0) -> tuple[bool, str | None]:
        if url == "http://ticktick-mcp:8000/mcp":
            return False, "dns"
        if url == "http://localhost:8002/mcp":
            return True, None
        return False, f"unexpected:{url}"

    monkeypatch.setattr(ticktick_mcp, "probe_ticktick_mcp_endpoint", _probe)
    assert get_ticktick_mcp_url() == "http://localhost:8002/mcp"


def test_probe_ticktick_mcp_endpoint_rejects_unsupported_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ticktick_mcp.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, None)],
    )

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        ticktick_mcp.socket,
        "create_connection",
        lambda *_args, **_kwargs: _Conn(),
    )

    class _Response:
        status_code = 501

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return _Response()

    monkeypatch.setattr(ticktick_mcp.httpx, "Client", _Client)

    ok, reason = probe_ticktick_mcp_endpoint(
        "http://localhost:8002/mcp", connect_timeout_s=0.1
    )
    assert ok is False
    assert reason is not None
    assert "does not support MCP POST" in reason


@pytest.mark.asyncio
async def test_get_tools_raises_when_loader_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autogen_ext.tools.mcp as mcp_mod

    async def _failing_loader(_params):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_mod, "mcp_server_tools", _failing_loader)
    monkeypatch.setattr(
        ticktick_mcp,
        "probe_ticktick_mcp_endpoint",
        lambda *_args, **_kwargs: (True, None),
    )
    client = TickTickMcpClient.__new__(TickTickMcpClient)
    client._params = object()
    client._server_url = "http://example.invalid/mcp"
    client._timeout = 1.0

    with pytest.raises(RuntimeError, match="Failed to load TickTick MCP tools"):
        await client.get_tools()


@pytest.mark.asyncio
async def test_get_tools_raises_when_loader_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autogen_ext.tools.mcp as mcp_mod

    async def _empty_loader(_params):
        return []

    monkeypatch.setattr(mcp_mod, "mcp_server_tools", _empty_loader)
    monkeypatch.setattr(
        ticktick_mcp,
        "probe_ticktick_mcp_endpoint",
        lambda *_args, **_kwargs: (True, None),
    )
    client = TickTickMcpClient.__new__(TickTickMcpClient)
    client._params = object()
    client._server_url = "http://example.invalid/mcp"
    client._timeout = 1.0

    with pytest.raises(RuntimeError, match="Failed to load TickTick MCP tools"):
        await client.get_tools()


@pytest.mark.asyncio
async def test_get_tools_raises_when_endpoint_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ticktick_mcp,
        "probe_ticktick_mcp_endpoint",
        lambda *_args, **_kwargs: (False, "endpoint unavailable"),
    )
    client = TickTickMcpClient.__new__(TickTickMcpClient)
    client._params = object()
    client._server_url = "http://example.invalid/mcp"
    client._timeout = 1.0

    with pytest.raises(RuntimeError, match="Failed to load TickTick MCP tools"):
        await client.get_tools()
