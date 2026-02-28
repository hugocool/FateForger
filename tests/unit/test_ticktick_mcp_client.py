from __future__ import annotations

import pytest

from fateforger.tools.ticktick_mcp import TickTickMcpClient


pytest.importorskip("autogen_ext.tools.mcp")


@pytest.mark.asyncio
async def test_get_tools_raises_when_loader_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autogen_ext.tools.mcp as mcp_mod

    async def _failing_loader(_params):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_mod, "mcp_server_tools", _failing_loader)
    client = TickTickMcpClient.__new__(TickTickMcpClient)
    client._params = object()
    client._server_url = "http://ticktick-mcp:8000/mcp"
    client._timeout = 1.0
    monkeypatch.setattr(
        "fateforger.tools.ticktick_mcp.probe_ticktick_mcp_endpoint",
        lambda *_args, **_kwargs: (True, ""),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await client.get_tools()


@pytest.mark.asyncio
async def test_get_tools_raises_when_loader_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autogen_ext.tools.mcp as mcp_mod

    async def _empty_loader(_params):
        return []

    monkeypatch.setattr(mcp_mod, "mcp_server_tools", _empty_loader)
    client = TickTickMcpClient.__new__(TickTickMcpClient)
    client._params = object()
    client._server_url = "http://ticktick-mcp:8000/mcp"
    client._timeout = 1.0
    monkeypatch.setattr(
        "fateforger.tools.ticktick_mcp.probe_ticktick_mcp_endpoint",
        lambda *_args, **_kwargs: (True, ""),
    )

    with pytest.raises(RuntimeError, match="returned no tools"):
        await client.get_tools()
