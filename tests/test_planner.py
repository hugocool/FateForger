"""
Tests for the Planner Bot functionality.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from productivity_bot.planner_bot import PlannerBot


class TestPlannerBot:
    """Test the PlannerBot class."""

    def test_planner_bot_initialization(self, test_config, mock_slack_app):
        """Test PlannerBot initialization."""
        with patch("productivity_bot.planner_bot.AsyncApp") as mock_app_class:
            mock_app_class.return_value = mock_slack_app

            bot = PlannerBot(test_config)

            assert bot.config == test_config
            assert bot.app == mock_slack_app
            mock_app_class.assert_called_once_with(
                token=test_config.slack_bot_token,
                signing_secret=test_config.slack_signing_secret,
            )




