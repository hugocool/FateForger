from __future__ import annotations

import pytest

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


def test_settings_accepts_slack_user_token(monkeypatch) -> None:
    """Settings accepts SLACK_USER_TOKEN from environment without validation errors."""
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test-token")
    settings = Settings()
    assert settings.slack_user_token == "xoxp-test-token"


def test_settings_rejects_invalid_slack_user_token(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_USER_TOKEN", "invalid-token")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_invalid_mcp_calendar_url(monkeypatch) -> None:
    monkeypatch.setenv("MCP_CALENDAR_SERVER_URL", "localhost:3000")
    with pytest.raises(ValueError):
        Settings()


def test_settings_requires_socket_mode(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_SOCKET_MODE", "false")
    with pytest.raises(ValueError):
        Settings()
