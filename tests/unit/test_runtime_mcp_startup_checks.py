from __future__ import annotations

import pytest

from fateforger.core import runtime as runtime_module


def test_resolve_runtime_git_identity_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        ("rev-parse", "--abbrev-ref", "HEAD"): "issue/103-planning-reminder-hardening",
        ("rev-parse", "--short", "HEAD"): "abc1234",
        ("describe", "--tags", "--exact-match"): "v1.2.3",
        ("status", "--porcelain"): " M src/file.py",
    }

    def _fake_run_git_command(*args: str) -> str:
        return values[args]

    monkeypatch.setattr(runtime_module, "_run_git_command", _fake_run_git_command)

    identity = runtime_module._resolve_runtime_git_identity()  # noqa: SLF001

    assert identity.branch == "issue/103-planning-reminder-hardening"
    assert identity.commit == "abc1234"
    assert identity.tag == "v1.2.3"
    assert identity.dirty is True


def test_resolve_runtime_git_identity_falls_back_on_git_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_run_git_command(*args: str) -> str:
        raise RuntimeError(f"git failed for {args}")

    monkeypatch.setattr(runtime_module, "_run_git_command", _fake_run_git_command)

    identity = runtime_module._resolve_runtime_git_identity()  # noqa: SLF001

    assert identity.branch == "unknown"
    assert identity.commit == "unknown"
    assert identity.tag == "none"
    assert identity.dirty is False


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


def test_graphiti_backend_required_when_timeboxing_backend_is_graphiti(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module.settings, "timeboxing_memory_backend", "graphiti", raising=False
    )
    monkeypatch.setattr(
        runtime_module.settings,
        "tasks_defaults_memory_backend",
        "graphiti",
        raising=False,
    )
    assert runtime_module._graphiti_backend_required() is True  # noqa: SLF001


def test_graphiti_backend_required_when_tasks_backend_is_graphiti(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module.settings, "timeboxing_memory_backend", "graphiti", raising=False
    )
    monkeypatch.setattr(
        runtime_module.settings,
        "tasks_defaults_memory_backend",
        "graphiti",
        raising=False,
    )
    assert runtime_module._graphiti_backend_required() is True  # noqa: SLF001


async def test_assert_graphiti_runtime_available_noops_when_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "_graphiti_backend_required", lambda: False)
    await runtime_module._assert_graphiti_runtime_available()  # noqa: SLF001


async def test_assert_graphiti_runtime_available_passes_with_queryable_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "_graphiti_backend_required", lambda: True)

    import fateforger.agents.timeboxing.durable_constraint_store as durable_store_mod
    import fateforger.agents.timeboxing.graphiti_constraint_memory as graphiti_mod

    class _Store:
        async def get_store_info(self) -> dict[str, object]:
            return {
                "backend": "graphiti",
                "mcp_server_url": "http://localhost:8005/mcp",
            }

        async def query_constraints(self, *, filters, limit):  # noqa: ANN001
            assert isinstance(filters, dict)
            assert limit == 1
            return []

    monkeypatch.setattr(
        graphiti_mod,
        "build_graphiti_client_from_settings",
        lambda *, user_id: object(),
    )
    monkeypatch.setattr(
        durable_store_mod,
        "build_durable_constraint_store",
        lambda _client: _Store(),
    )

    await runtime_module._assert_graphiti_runtime_available()  # noqa: SLF001


async def test_assert_graphiti_runtime_available_raises_with_clear_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "_graphiti_backend_required", lambda: True)

    import fateforger.agents.timeboxing.graphiti_constraint_memory as graphiti_mod

    def _boom(*, user_id: str) -> object:
        raise RuntimeError("graphiti runtime unavailable")

    monkeypatch.setattr(graphiti_mod, "build_graphiti_client_from_settings", _boom)

    with pytest.raises(RuntimeError) as exc_info:
        await runtime_module._assert_graphiti_runtime_available()  # noqa: SLF001

    message = str(exc_info.value)
    assert "Graphiti startup dependency check failed" in message
    assert "graphiti runtime unavailable" in message
