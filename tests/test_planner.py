"""Tests for the Planner Bot functionality."""

import pytest

from productivity_bot.planner_bot import PlannerBot


class TestPlannerBot:
    """Test the PlannerBot class."""

    def test_planner_bot_initialization(self, planner_bot, mock_slack_app, test_config):
        """Ensure PlannerBot initializes with provided config and Slack app."""
        assert isinstance(planner_bot, PlannerBot)
        assert planner_bot.config == test_config
        assert planner_bot.app == mock_slack_app
