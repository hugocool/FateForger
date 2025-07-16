"""
Tests for common utilities and configuration.
"""

import os
from unittest.mock import patch

import pytest
from productivity_bot.common import Config


class TestConfig:
    """Test the Config class."""

    def test_config_initialization(self, test_config):
        """Test config is properly initialized."""
        assert test_config.slack_bot_token == "xoxb-test-token"
        assert test_config.slack_signing_secret == "test-signing-secret"
        assert test_config.slack_app_token == "xapp-test-app-token"
        assert test_config.openai_api_key == "test-openai-key"

    def test_missing_required_vars(self):
        """Missing environment variables raise validation errors."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(Exception):
                Config()

    def test_config_missing_optional_vars(self, mock_env_vars):
        """Optional variables can be absent without error."""
        env_vars = mock_env_vars.copy()
        del env_vars["OPENAI_API_KEY"]

        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(Exception):
                Config()


