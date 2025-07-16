"""
Tests for common utilities and configuration.
"""

import pytest
import os
from unittest.mock import patch, Mock
from productivity_bot.common import (
    Config,
    get_timestamp,
    safe_get_env,
    format_slack_message,
)


class TestConfig:
    """Test the Config class."""

    def test_config_initialization(self, test_config):
        """Test config is properly initialized."""
        assert test_config.slack_bot_token == "xoxb-test-token"
        assert test_config.slack_signing_secret == "test-signing-secret"
        assert test_config.slack_app_token == "xapp-test-app-token"
        assert test_config.port == 8000
        assert test_config.debug is True

    def test_config_validation_success(self, test_config):
        """Test config validation with all required vars."""
        assert test_config.validate() is True

    def test_config_validation_failure(self):
        """Test config validation with missing vars."""
        with patch.dict(os.environ, {}, clear=True):
            config = Config()
            assert config.validate() is False

    def test_config_missing_optional_vars(self, mock_env_vars):
        """Test config with missing optional variables."""
        env_vars = mock_env_vars.copy()
        del env_vars["OPENAI_API_KEY"]

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config()
            assert config.openai_api_key is None
            assert config.validate() is True  # Should still be valid


class TestUtilityFunctions:
    """Test utility functions."""

    def test_get_timestamp(self):
        """Test timestamp generation."""
        timestamp = get_timestamp()
        assert isinstance(timestamp, str)
        assert len(timestamp) == 19  # YYYY-MM-DD HH:MM:SS format

    def test_safe_get_env_existing(self):
        """Test safe_get_env with existing variable."""
        with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
            result = safe_get_env("TEST_VAR")
            assert result == "test_value"

    def test_safe_get_env_missing_with_default(self):
        """Test safe_get_env with missing variable and default."""
        result = safe_get_env("NONEXISTENT_VAR", "default_value")
        assert result == "default_value"

    def test_safe_get_env_missing_no_default(self):
        """Test safe_get_env with missing variable and no default."""
        result = safe_get_env("NONEXISTENT_VAR")
        assert result is None

    def test_format_slack_message(self):
        """Test Slack message formatting."""
        message = "Test message"
        result = format_slack_message(message)

        assert result["text"] == message
        assert result["username"] == "ProductivityBot"
        assert result["icon_emoji"] == ":robot_face:"
        assert "timestamp" in result

    def test_format_slack_message_custom_username(self):
        """Test Slack message formatting with custom username."""
        message = "Test message"
        username = "CustomBot"
        result = format_slack_message(message, username)

        assert result["text"] == message
        assert result["username"] == username
        assert result["icon_emoji"] == ":robot_face:"
