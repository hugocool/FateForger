"""
Tests for the Planner Bot functionality.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from productivity_bot.planner_bot import PlannerBot


class TestPlannerBot:
    """Test the PlannerBot class."""

    def test_planner_bot_initialization(self, test_config, mock_slack_app):
        """Test PlannerBot initialization."""
        with patch("productivity_bot.planner_bot.App") as mock_app_class:
            mock_app_class.return_value = mock_slack_app

            bot = PlannerBot(test_config)

            assert bot.config == test_config
            assert bot.app == mock_slack_app
            mock_app_class.assert_called_once_with(
                token=test_config.slack_bot_token,
                signing_secret=test_config.slack_signing_secret,
            )

    def test_planner_bot_invalid_config(self):
        """Test PlannerBot with invalid config."""
        invalid_config = Mock()
        invalid_config.validate.return_value = False

        with pytest.raises(ValueError, match="Invalid configuration"):
            PlannerBot(invalid_config)

    def test_extract_planning_context(self, test_config, mock_slack_app):
        """Test planning context extraction."""
        with patch("productivity_bot.planner_bot.App") as mock_app_class:
            mock_app_class.return_value = mock_slack_app

            bot = PlannerBot(test_config)
            context = bot._extract_planning_context("I have meetings at 2pm and 4pm")

            assert isinstance(context, dict)
            assert "raw_text" in context
            assert context["raw_text"] == "I have meetings at 2pm and 4pm"
            assert "tasks" in context
            assert "time_constraints" in context
            assert "priorities" in context

    def test_generate_plan_with_text(self, test_config, mock_slack_app):
        """Test plan generation with context."""
        with patch("productivity_bot.planner_bot.App") as mock_app_class:
            mock_app_class.return_value = mock_slack_app

            bot = PlannerBot(test_config)
            context = {"raw_text": "I need to finish my presentation"}
            plan = bot._generate_plan(context)

            assert isinstance(plan, str)
            assert "Time Blocks" in plan
            assert "Suggestions" in plan
            assert len(plan) > 0

    def test_generate_plan_empty_context(self, test_config, mock_slack_app):
        """Test plan generation with empty context."""
        with patch("productivity_bot.planner_bot.App") as mock_app_class:
            mock_app_class.return_value = mock_slack_app

            bot = PlannerBot(test_config)
            context = {"raw_text": ""}
            plan = bot._generate_plan(context)

            assert "I need more information" in plan

    def test_start_without_app_token(self, test_config, mock_slack_app):
        """Test starting bot without app token."""
        test_config.slack_app_token = None

        with patch("productivity_bot.planner_bot.App") as mock_app_class:
            mock_app_class.return_value = mock_slack_app

            bot = PlannerBot(test_config)

            with pytest.raises(ValueError, match="SLACK_APP_TOKEN is required"):
                bot.start()


class TestPlannerBotHandlers:
    """Test PlannerBot message handlers."""

    @pytest.fixture
    def bot_with_handlers(self, test_config, mock_slack_app):
        """Create a bot instance with mocked handlers."""
        with patch("productivity_bot.planner_bot.App") as mock_app_class:
            mock_app_class.return_value = mock_slack_app
            bot = PlannerBot(test_config)
            return bot

    def test_plan_message_handler_registration(self, bot_with_handlers):
        """Test that message handlers are registered."""
        # Check that the app.message decorator was called
        bot_with_handlers.app.message.assert_called()

    def test_plan_command_handler_registration(self, bot_with_handlers):
        """Test that command handlers are registered."""
        # Check that the app.command decorator was called
        bot_with_handlers.app.command.assert_called()
