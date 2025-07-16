"""
Tests for haunter_bot.py with focus on haunt_user implementation.

This test suite validates the complete haunt_user functionality including:
1. Session completion detection and cleanup
2. First haunt attempt with Slack scheduled messages
3. Subsequent haunt escalation with exponential backoff
4. APScheduler job management and cancellation
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from productivity_bot.common import backoff_minutes
from productivity_bot.database import PlanningSessionService
from productivity_bot.haunter_bot import haunt_user
from productivity_bot.models import PlanningSession, PlanStatus


class TestHauntUser:
    """Test suite for the haunt_user function."""

    @pytest.fixture
    def mock_session_complete(self):
        """Create a mock session with COMPLETE status for testing cancellation."""
        session = PlanningSession(
            id=1,
            user_id="U12345",
            date=date.today(),
            scheduled_for=datetime.now(timezone.utc),
            status=PlanStatus.COMPLETE,
            slack_scheduled_message_id="SM123456",
            haunt_attempt=1,
            scheduler_job_id="job_123",
        )
        return session

    @pytest.fixture
    def mock_session_in_progress(self):
        """Create a mock session with IN_PROGRESS status for testing haunt execution."""
        session = PlanningSession(
            id=2,
            user_id="U67890",
            date=date.today(),
            scheduled_for=datetime.now(timezone.utc),
            status=PlanStatus.IN_PROGRESS,
            slack_scheduled_message_id=None,
            haunt_attempt=0,
            scheduler_job_id=None,
        )
        return session

    @pytest.fixture
    def mock_session_escalation(self):
        """Create a mock session for testing escalation logic."""
        session = PlanningSession(
            id=3,
            user_id="U54321",
            date=date.today(),
            scheduled_for=datetime.now(timezone.utc),
            status=PlanStatus.NOT_STARTED,
            slack_scheduled_message_id="SM999888",
            haunt_attempt=1,
            scheduler_job_id="job_456",
        )
        return session

    @pytest.mark.asyncio
    async def test_session_complete_cancels_reminders(self, mock_session_complete):
        """Test that COMPLETE sessions cancel APScheduler jobs and Slack scheduled messages."""

        # Mock the service and dependencies
        with (
            patch(
                "productivity_bot.database.PlanningSessionService.get_session_by_id"
            ) as mock_get_session,
            patch("productivity_bot.scheduler.cancel_user_haunt") as mock_cancel_job,
            patch("slack_bolt.async_app.AsyncApp") as mock_app_class,
        ):

            # Setup mocks
            mock_get_session.return_value = mock_session_complete

            mock_app = AsyncMock()
            mock_app_class.return_value = mock_app
            mock_app.client.chat_deleteScheduledMessage = AsyncMock()

            # Execute the function
            await haunt_user(mock_session_complete.id)

            # Verify cancellation logic
            mock_cancel_job.assert_called_once_with(mock_session_complete.id)
            mock_app.client.chat_deleteScheduledMessage.assert_called_once_with(
                channel=mock_session_complete.user_id,
                scheduled_message_id=mock_session_complete.slack_scheduled_message_id,
            )

    @pytest.mark.asyncio
    async def test_first_haunt_schedules_message(self, mock_session_in_progress):
        """Test that the first haunt attempt schedules a Slack message and next job."""

        # Mock current time for predictable scheduling
        mock_now = datetime.now(timezone.utc)

        with (
            patch(
                "productivity_bot.database.PlanningSessionService.get_session_by_id"
            ) as mock_get_session,
            patch(
                "productivity_bot.database.PlanningSessionService.update_session"
            ) as mock_update_session,
            patch(
                "productivity_bot.scheduler.schedule_user_haunt"
            ) as mock_schedule_job,
            patch("productivity_bot.scheduler.cancel_user_haunt") as mock_cancel_job,
            patch("slack_bolt.async_app.AsyncApp") as mock_app_class,
            patch("datetime.datetime") as mock_datetime,
        ):

            # Setup mocks
            mock_get_session.return_value = mock_session_in_progress
            mock_update_session.return_value = True
            mock_schedule_job.return_value = "new_job_456"

            mock_datetime.now.return_value = mock_now

            mock_app = AsyncMock()
            mock_app_class.return_value = mock_app
            mock_app.client.chat_postMessage = AsyncMock(return_value={"ok": True})
            mock_app.client.chat_scheduleMessage = AsyncMock(
                return_value={"scheduled_message_id": "SM789123"}
            )

            # Execute the function
            await haunt_user(mock_session_in_progress.id)

            # Verify immediate Slack message for first attempt
            mock_app.client.chat_postMessage.assert_called_once_with(
                channel=mock_session_in_progress.user_id,
                text="⏰ It's time to plan your day! Please open your planning session.",
            )

            # Verify APScheduler job management
            mock_cancel_job.assert_called_once_with(mock_session_in_progress.id)
            mock_schedule_job.assert_called_once()

            # Verify session updates
            assert mock_session_in_progress.haunt_attempt == 1
            assert mock_session_in_progress.scheduler_job_id == "new_job_456"
            mock_update_session.assert_called_once_with(mock_session_in_progress)

    @pytest.mark.asyncio
    async def test_subsequent_haunt_escalation(self, mock_session_escalation):
        """Test that subsequent haunts use escalated messaging and proper backoff."""

        mock_now = datetime.now(timezone.utc)

        with (
            patch(
                "productivity_bot.database.PlanningSessionService.get_session_by_id"
            ) as mock_get_session,
            patch(
                "productivity_bot.database.PlanningSessionService.update_session"
            ) as mock_update_session,
            patch(
                "productivity_bot.scheduler.schedule_user_haunt"
            ) as mock_schedule_job,
            patch("productivity_bot.scheduler.cancel_user_haunt") as mock_cancel_job,
            patch("slack_bolt.async_app.AsyncApp") as mock_app_class,
            patch("datetime.datetime") as mock_datetime,
        ):

            # Setup mocks
            mock_get_session.return_value = mock_session_escalation
            mock_update_session.return_value = True
            mock_schedule_job.return_value = "escalated_job_789"

            mock_datetime.now.return_value = mock_now

            mock_app = AsyncMock()
            mock_app_class.return_value = mock_app
            mock_app.client.chat_scheduleMessage = AsyncMock(
                return_value={"scheduled_message_id": "SM999000"}
            )

            # Execute the function
            await haunt_user(mock_session_escalation.id)

            # Verify escalated message scheduled with buffer time (10 seconds from now)
            expected_schedule_time = mock_now + timedelta(seconds=10)
            mock_app.client.chat_scheduleMessage.assert_called_once_with(
                channel=mock_session_escalation.user_id,
                text="⏰ Reminder 2: don't forget to plan tomorrow's schedule!",
                post_at=int(expected_schedule_time.timestamp()),
            )

            # Verify updated session fields
            assert mock_session_escalation.slack_scheduled_message_id == "SM999000"
            assert mock_session_escalation.haunt_attempt == 2
            assert mock_session_escalation.scheduler_job_id == "escalated_job_789"

            # Verify scheduling with proper backoff delay (attempt 2 -> 20 minutes)
            expected_delay = backoff_minutes(2)
            assert expected_delay == 20  # Verify backoff logic

            # Verify the next job is scheduled ~20 minutes in the future
            schedule_call_args = mock_schedule_job.call_args
            scheduled_session_id, scheduled_time = schedule_call_args[0]
            assert scheduled_session_id == mock_session_escalation.id

            # Allow some tolerance for timing (within 1 minute)
            expected_time = mock_now + timedelta(minutes=20)
            time_diff = abs((scheduled_time - expected_time).total_seconds())
            assert (
                time_diff < 60
            ), f"Scheduled time {scheduled_time} not within expected range of {expected_time}"

    @pytest.mark.asyncio
    async def test_session_not_found_logs_warning(self):
        """Test that missing sessions are handled gracefully with logging."""

        with (
            patch(
                "productivity_bot.database.PlanningSessionService.get_session_by_id"
            ) as mock_get_session,
            patch("logging.getLogger") as mock_get_logger,
        ):

            # Setup mocks
            mock_get_session.return_value = None
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            # Execute the function
            await haunt_user(999)

            # Verify warning is logged
            mock_logger.warning.assert_called_once_with(
                "haunt_user: session 999 not found"
            )

    @pytest.mark.asyncio
    async def test_slack_api_error_handling(self, mock_session_in_progress):
        """Test that Slack API errors are handled gracefully."""

        from slack_sdk.errors import SlackApiError

        mock_now = datetime.now(timezone.utc)

        with (
            patch(
                "productivity_bot.database.PlanningSessionService.get_session_by_id"
            ) as mock_get_session,
            patch("slack_bolt.async_app.AsyncApp") as mock_app_class,
            patch("datetime.datetime") as mock_datetime,
            patch("logging.getLogger") as mock_get_logger,
        ):

            # Setup mocks
            mock_get_session.return_value = mock_session_in_progress
            mock_datetime.now.return_value = mock_now

            mock_app = AsyncMock()
            mock_app_class.return_value = mock_app
            mock_app.client.chat_postMessage = AsyncMock(
                side_effect=SlackApiError("API Error", response=MagicMock())
            )

            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            # Execute the function
            await haunt_user(mock_session_in_progress.id)

            # Verify error is logged and function returns early
            mock_logger.error.assert_called()
            error_call_args = mock_logger.error.call_args[0][0]
            assert "Slack message failed" in error_call_args


class TestBackoffMinutes:
    """Test suite for the backoff_minutes function."""

    def test_backoff_progression(self):
        """Test the exponential backoff progression."""
        assert backoff_minutes(0) == 5  # Initial delay
        assert backoff_minutes(1) == 10  # Second attempt
        assert backoff_minutes(2) == 20  # Third attempt
        assert backoff_minutes(3) == 40  # Fourth attempt
        assert backoff_minutes(4) == 60  # Capped at 60 minutes
        assert backoff_minutes(10) == 60  # Still capped at 60 minutes

    def test_negative_attempt_handling(self):
        """Test that negative attempts default to initial delay."""
        assert backoff_minutes(-1) == 5
        assert backoff_minutes(-5) == 5


if __name__ == "__main__":
    pytest.main([__file__])
