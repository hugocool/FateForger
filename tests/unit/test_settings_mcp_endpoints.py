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


def test_settings_accepts_slack_test_user_token(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_TEST_USER_TOKEN", "xoxp-test-user-token")
    settings = Settings()
    assert settings.slack_test_user_token == "xoxp-test-user-token"


def test_settings_rejects_invalid_slack_user_token(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_USER_TOKEN", "invalid-token")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_invalid_slack_test_user_token(monkeypatch) -> None:
    monkeypatch.setenv("SLACK_TEST_USER_TOKEN", "invalid-token")
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


def test_settings_rejects_unknown_timeboxing_memory_backend(monkeypatch) -> None:
    monkeypatch.setenv("TIMEBOXING_MEMORY_BACKEND", "invalid-backend")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_constraint_mcp_timeboxing_memory_backend(monkeypatch) -> None:
    monkeypatch.setenv("TIMEBOXING_MEMORY_BACKEND", "constraint_mcp")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_graphiti_without_mcp_url(monkeypatch) -> None:
    monkeypatch.setenv("TIMEBOXING_MEMORY_BACKEND", "graphiti")
    monkeypatch.setenv("GRAPHITI_MCP_SERVER_URL", "")
    with pytest.raises(ValueError):
        Settings()


def test_settings_accepts_graphiti_with_mcp_runtime_config(monkeypatch) -> None:
    monkeypatch.setenv("TIMEBOXING_MEMORY_BACKEND", "graphiti")
    monkeypatch.setenv("GRAPHITI_MCP_SERVER_URL", "http://localhost:8005/mcp")
    settings = Settings()
    assert settings.timeboxing_memory_backend == "graphiti"


def test_settings_rejects_invalid_graphiti_mcp_url(monkeypatch) -> None:
    monkeypatch.setenv("TIMEBOXING_MEMORY_BACKEND", "graphiti")
    monkeypatch.setenv("GRAPHITI_MCP_SERVER_URL", "graphiti-mcp:8000")
    with pytest.raises(ValueError):
        Settings()


def test_settings_accepts_tasks_defaults_graphiti_backend(monkeypatch) -> None:
    monkeypatch.setenv("TASKS_DEFAULTS_MEMORY_BACKEND", "graphiti")
    monkeypatch.setenv("GRAPHITI_MCP_SERVER_URL", "http://localhost:8005/mcp")
    settings = Settings()
    assert settings.tasks_defaults_memory_backend == "graphiti"


def test_settings_rejects_tasks_defaults_graphiti_without_mcp_url(monkeypatch) -> None:
    monkeypatch.setenv("TIMEBOXING_MEMORY_BACKEND", "graphiti")
    monkeypatch.setenv("TASKS_DEFAULTS_MEMORY_BACKEND", "graphiti")
    monkeypatch.setenv("GRAPHITI_MCP_SERVER_URL", "")
    with pytest.raises(ValueError):
        Settings()


@pytest.mark.parametrize(
    "legacy_backend",
    ["mem0", "constraint_mcp", "inherit_timeboxing", "disabled"],
)
def test_settings_rejects_non_graphiti_tasks_defaults_backend(
    monkeypatch,
    legacy_backend: str,
) -> None:
    monkeypatch.setenv("TIMEBOXING_MEMORY_BACKEND", "graphiti")
    monkeypatch.setenv("TASKS_DEFAULTS_MEMORY_BACKEND", legacy_backend)
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_invalid_notion_mcp_url(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_MCP_URL", "localhost:3001")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_invalid_ticktick_mcp_url(monkeypatch) -> None:
    monkeypatch.setenv("TICKTICK_MCP_URL", "ticktick-mcp:8000/mcp")
    with pytest.raises(ValueError):
        Settings()


def test_settings_accepts_autogen_event_logging_directives(monkeypatch) -> None:
    monkeypatch.setenv("AUTOGEN_EVENTS_LOG", "summary")
    monkeypatch.setenv("AUTOGEN_EVENTS_OUTPUT_TARGET", "audit")
    monkeypatch.setenv("AUTOGEN_EVENTS_FULL_PAYLOAD_MODE", "sanitized")
    settings = Settings()
    assert settings.autogen_events_log == "summary"
    assert settings.autogen_events_output_target == "audit"
    assert settings.autogen_events_full_payload_mode == "sanitized"


def test_settings_rejects_invalid_autogen_events_log(monkeypatch) -> None:
    monkeypatch.setenv("AUTOGEN_EVENTS_LOG", "verbose")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_invalid_autogen_events_output_target(monkeypatch) -> None:
    monkeypatch.setenv("AUTOGEN_EVENTS_OUTPUT_TARGET", "file")
    with pytest.raises(ValueError):
        Settings()


def test_settings_rejects_invalid_autogen_events_full_payload_mode(monkeypatch) -> None:
    monkeypatch.setenv("AUTOGEN_EVENTS_FULL_PAYLOAD_MODE", "safe")
    with pytest.raises(ValueError):
        Settings()
