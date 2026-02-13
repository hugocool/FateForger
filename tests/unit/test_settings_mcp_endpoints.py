from __future__ import annotations

from fateforger.core.config import Settings


def test_settings_mcp_calendar_server_url_default(monkeypatch) -> None:
    """Settings defaults to localhost Calendar MCP when env is unset."""
    monkeypatch.delenv("MCP_CALENDAR_SERVER_URL", raising=False)
    settings = Settings()
    assert settings.mcp_calendar_server_url == "http://localhost:3000"


def test_settings_mcp_calendar_server_url_from_env(monkeypatch) -> None:
    """Settings reads Calendar MCP URL from environment variable."""
    monkeypatch.setenv("MCP_CALENDAR_SERVER_URL", "http://example:1234")
    settings = Settings()
    assert settings.mcp_calendar_server_url == "http://example:1234"


def test_settings_mcp_calendar_server_url_docker_from_env(monkeypatch) -> None:
    """Settings reads Docker-network Calendar MCP URL from environment variable."""
    monkeypatch.setenv("MCP_CALENDAR_SERVER_URL_DOCKER", "http://calendar-mcp:9999")
    settings = Settings()
    assert settings.mcp_calendar_server_url_docker == "http://calendar-mcp:9999"
