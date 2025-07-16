"""
Tests for the Haunter Bot functionality.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock
from productivity_bot.haunter_bot import HaunterBot


class TestHaunterBot:
    """Test the HaunterBot class."""

    def test_haunter_bot_initialization(self, test_config, mock_slack_app):
        """Test HaunterBot initialization."""
        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch(
                "productivity_bot.haunter_bot.AsyncIOScheduler"
            ) as mock_scheduler_class,
        ):

            mock_app_class.return_value = mock_slack_app
            mock_scheduler = Mock()
            mock_scheduler_class.return_value = mock_scheduler

            bot = HaunterBot(test_config)

            assert bot.config == test_config
            assert bot.app == mock_slack_app
            assert bot.scheduler == mock_scheduler
            assert isinstance(bot.reminders, dict)

    def test_haunter_bot_invalid_config(self):
        """Test HaunterBot with invalid config."""
        invalid_config = Mock()
        invalid_config.validate.return_value = False

        with pytest.raises(ValueError, match="Invalid configuration"):
            HaunterBot(invalid_config)

    def test_parse_reminder_valid_input(self, test_config, mock_slack_app):
        """Test reminder parsing with valid input."""
        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch("productivity_bot.haunter_bot.AsyncIOScheduler"),
        ):

            mock_app_class.return_value = mock_slack_app
            bot = HaunterBot(test_config)

            text = "remind me to submit report in 30 minutes"
            user_id = "U123456789"

            result = bot._parse_reminder(text, user_id)

            assert result is not None
            assert result["task"] == "submit report"
            assert result["user_id"] == user_id
            assert "when" in result
            assert "timestamp" in result

    def test_parse_reminder_hours(self, test_config, mock_slack_app):
        """Test reminder parsing with hours."""
        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch("productivity_bot.haunter_bot.AsyncIOScheduler"),
        ):

            mock_app_class.return_value = mock_slack_app
            bot = HaunterBot(test_config)

            text = "remind me to call client in 2 hours"
            user_id = "U123456789"

            result = bot._parse_reminder(text, user_id)

            assert result is not None
            assert result["task"] == "call client"
            assert result["user_id"] == user_id

    def test_parse_reminder_days(self, test_config, mock_slack_app):
        """Test reminder parsing with days."""
        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch("productivity_bot.haunter_bot.AsyncIOScheduler"),
        ):

            mock_app_class.return_value = mock_slack_app
            bot = HaunterBot(test_config)

            text = "remind prepare presentation in 1 day"
            user_id = "U123456789"

            result = bot._parse_reminder(text, user_id)

            assert result is not None
            assert result["task"] == "prepare presentation"
            assert result["user_id"] == user_id

    def test_parse_reminder_invalid_input(self, test_config, mock_slack_app):
        """Test reminder parsing with invalid input."""
        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch("productivity_bot.haunter_bot.AsyncIOScheduler"),
        ):

            mock_app_class.return_value = mock_slack_app
            bot = HaunterBot(test_config)

            text = "this is not a valid reminder"
            user_id = "U123456789"

            result = bot._parse_reminder(text, user_id)

            assert result is None

    def test_schedule_reminder(self, test_config, mock_slack_app):
        """Test reminder scheduling."""
        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch(
                "productivity_bot.haunter_bot.AsyncIOScheduler"
            ) as mock_scheduler_class,
        ):

            mock_app_class.return_value = mock_slack_app
            mock_scheduler = Mock()
            mock_scheduler_class.return_value = mock_scheduler

            bot = HaunterBot(test_config)

            reminder_details = {
                "task": "test task",
                "when": "2023-07-16 15:00:00",
                "user_id": "U123456789",
                "timestamp": datetime.now() + timedelta(hours=1),
            }

            bot._schedule_reminder(reminder_details)

            # Check that reminder was stored
            assert len(bot.reminders) == 1

            # Check that scheduler job was added
            mock_scheduler.add_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_reminder(self, test_config, mock_slack_app):
        """Test sending a reminder."""
        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch("productivity_bot.haunter_bot.AsyncIOScheduler"),
        ):

            mock_app_class.return_value = mock_slack_app
            mock_slack_app.client.chat_postMessage = AsyncMock()

            bot = HaunterBot(test_config)

            # Add a reminder
            reminder_id = "test_reminder"
            bot.reminders[reminder_id] = {"task": "test task", "user_id": "U123456789"}

            await bot._send_reminder(reminder_id)

            # Check that message was sent
            mock_slack_app.client.chat_postMessage.assert_called_once()

            # Check that reminder was cleaned up
            assert reminder_id not in bot.reminders

    def test_start_without_app_token(self, test_config, mock_slack_app):
        """Test starting bot without app token."""
        test_config.slack_app_token = None

        with (
            patch("productivity_bot.haunter_bot.App") as mock_app_class,
            patch("productivity_bot.haunter_bot.AsyncIOScheduler"),
        ):

            mock_app_class.return_value = mock_slack_app
            bot = HaunterBot(test_config)

            with pytest.raises(ValueError, match="SLACK_APP_TOKEN is required"):
                bot.start()
