from __future__ import annotations

import pytest

from fateforger.core import runtime as runtime_module


async def test_assert_mcp_servers_available_passes_when_all_servers_discover_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = [
        runtime_module._McpStartupServer(
            name="calendar-mcp", url="http://calendar:3000"
        ),
        runtime_module._McpStartupServer(
            name="notion-mcp", url="http://notion:3001/mcp"
        ),
        runtime_module._McpStartupServer(
            name="ticktick-mcp", url="http://ticktick:8000/mcp"
        ),
    ]
    monkeypatch.setattr(runtime_module, "_runtime_mcp_servers", lambda: servers)

    async def _fake_discover(*, url: str, headers, timeout_s: float) -> list:
        assert timeout_s > 0
        assert headers is None
        assert url
        return [object()]

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _fake_discover)

    await runtime_module._assert_mcp_servers_available()  # noqa: SLF001


async def test_assert_mcp_servers_available_fails_loudly_with_server_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = [
        runtime_module._McpStartupServer(
            name="calendar-mcp", url="http://calendar:3000"
        ),
        runtime_module._McpStartupServer(
            name="notion-mcp", url="http://notion:3001/mcp"
        ),
    ]
    monkeypatch.setattr(runtime_module, "_runtime_mcp_servers", lambda: servers)

    async def _fake_discover(*, url: str, headers, timeout_s: float) -> list:
        if "notion" in url:
            raise RuntimeError("connection refused")
        return [object()]

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _fake_discover)

    with pytest.raises(RuntimeError) as exc_info:
        await runtime_module._assert_mcp_servers_available()  # noqa: SLF001

    message = str(exc_info.value)
    assert "MCP startup dependency check failed" in message
    assert "notion-mcp" in message
    assert "connection refused" in message
    assert "calendar-mcp" not in message


async def test_probe_runtime_mcp_server_handles_empty_tools_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = runtime_module._McpStartupServer(
        name="calendar-mcp",
        url="http://calendar:3000",
        timeout_s=1.0,
    )

    async def _empty_tools(*, url: str, headers, timeout_s: float) -> list:
        return []

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _empty_tools)
    empty_result = await runtime_module._probe_runtime_mcp_server(
        server
    )  # noqa: SLF001
    assert empty_result.ok is False
    assert empty_result.error == "server returned no tools"

    async def _timeout(*, url: str, headers, timeout_s: float) -> list:
        raise TimeoutError

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _timeout)
    timeout_result = await runtime_module._probe_runtime_mcp_server(
        server
    )  # noqa: SLF001
    assert timeout_result.ok is False
    assert "timed out" in (timeout_result.error or "")


def test_runtime_mcp_servers_uses_resolved_tool_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module.settings,
        "mcp_calendar_server_url",
        "http://calendar-from-settings:3000",
        raising=False,
    )

    # Ensure runtime probes use the same endpoint resolvers as the agents/tools.
    monkeypatch.setattr(
        "fateforger.tools.notion_mcp.get_notion_mcp_url",
        lambda: "http://notion-from-resolver:3001/mcp",
    )
    monkeypatch.setattr(
        "fateforger.tools.ticktick_mcp.get_ticktick_mcp_url",
        lambda: "http://ticktick-from-resolver:8000/mcp",
    )
    monkeypatch.setattr(
        "fateforger.tools.notion_mcp.get_notion_mcp_headers",
        lambda: {"Authorization": "Bearer test-token"},
    )

    servers = runtime_module._runtime_mcp_servers()  # noqa: SLF001
    assert [server.name for server in servers] == [
        "calendar-mcp",
        "notion-mcp",
        "ticktick-mcp",
    ]
    assert servers[0].url == "http://calendar-from-settings:3000"
    assert servers[1].url == "http://notion-from-resolver:3001/mcp"
    assert servers[1].headers == {"Authorization": "Bearer test-token"}
    assert servers[2].url == "http://ticktick-from-resolver:8000/mcp"
    # notion-mcp and ticktick-mcp are optional; calendar-mcp is required
    assert servers[0].optional is False
    assert servers[1].optional is True
    assert servers[2].optional is True


async def test_assert_mcp_servers_available_optional_failures_do_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional MCP servers that fail should log a warning but not abort startup."""
    servers = [
        runtime_module._McpStartupServer(
            name="calendar-mcp", url="http://calendar:3000"
        ),
        runtime_module._McpStartupServer(
            name="notion-mcp", url="http://notion:3001/mcp", optional=True
        ),
        runtime_module._McpStartupServer(
            name="ticktick-mcp", url="http://ticktick:8000/mcp", optional=True
        ),
    ]
    monkeypatch.setattr(runtime_module, "_runtime_mcp_servers", lambda: servers)

    async def _fake_discover(*, url: str, headers, timeout_s: float) -> list:
        if "notion" in url or "ticktick" in url:
            raise ConnectionError("service unavailable")
        return [object()]

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _fake_discover)

    # Must not raise even though two servers are unreachable
    await runtime_module._assert_mcp_servers_available()  # noqa: SLF001


async def test_assert_mcp_servers_available_required_failure_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A required server failure must still abort startup."""
    servers = [
        runtime_module._McpStartupServer(
            name="calendar-mcp", url="http://calendar:3000"
        ),
        runtime_module._McpStartupServer(
            name="notion-mcp", url="http://notion:3001/mcp", optional=True
        ),
    ]
    monkeypatch.setattr(runtime_module, "_runtime_mcp_servers", lambda: servers)

    async def _fake_discover(*, url: str, headers, timeout_s: float) -> list:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(runtime_module, "_discover_mcp_tools", _fake_discover)

    with pytest.raises(RuntimeError) as exc_info:
        await runtime_module._assert_mcp_servers_available()  # noqa: SLF001

    message = str(exc_info.value)
    assert "MCP startup dependency check failed" in message
    assert "calendar-mcp" in message
    # optional server should NOT appear in the hard-failure message
    assert "notion-mcp" not in message
