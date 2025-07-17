"""
Unit tests for BaseHaunter infrastructure and back-off engine.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient

from src.productivity_bot.actions.planner_action import PlannerAction
from src.productivity_bot.haunting.base_haunter import BaseHaunter


class TestHaunter(BaseHaunter):
    """Concrete implementation for testing."""

    async def _route_to_planner(self, intent: PlannerAction) -> bool:
        """Test implementation that logs the intent."""
        return True


class TestBaseHaunterBackoff:
    """Test exponential back-off logic."""

    def setup_method(self):
        """Set up test fixtures."""
        self.slack = AsyncMock(spec=AsyncWebClient)
        self.scheduler = MagicMock(spec=AsyncIOScheduler)
        self.haunter = TestHaunter(
            session_id=123, slack=self.slack, scheduler=self.scheduler
        )

    def test_next_delay_sequence(self):
        """Test back-off sequence: 5, 10, 20, 40, 80, 120, 120..."""
        expected_delays = [5, 10, 20, 40, 80, 120, 120, 120]

        for attempt, expected in enumerate(expected_delays):
            actual = self.haunter.next_delay(attempt)
            assert (
                actual == expected
            ), f"Attempt {attempt}: expected {expected}, got {actual}"

    def test_next_delay_custom_config(self):
        """Test back-off with custom base and cap."""
        self.haunter.backoff_base_minutes = 3
        self.haunter.backoff_cap_minutes = 60

        # Should be: 3, 6, 12, 24, 48, 60, 60...
        expected_delays = [3, 6, 12, 24, 48, 60, 60]

        for attempt, expected in enumerate(expected_delays):
            actual = self.haunter.next_delay(attempt)
            assert (
                actual == expected
            ), f"Attempt {attempt}: expected {expected}, got {actual}"

    def test_next_run_time(self):
        """Test that next_run_time adds correct delay to current time."""
        with patch("src.productivity_bot.haunting.base_haunter.datetime") as mock_dt:
            base_time = datetime(2025, 1, 22, 12, 0, 0)
            mock_dt.utcnow.return_value = base_time

            # Attempt 0 should add 5 minutes
            next_run = self.haunter.next_run_time(0)
            expected = base_time + timedelta(minutes=5)

            assert next_run == expected


class TestBaseHaunterScheduling:
    """Test APScheduler job management."""

    def setup_method(self):
        """Set up test fixtures."""
        self.slack = AsyncMock(spec=AsyncWebClient)
        self.scheduler = MagicMock(spec=AsyncIOScheduler)
        self.haunter = TestHaunter(
            session_id=123, slack=self.slack, scheduler=self.scheduler
        )

    def test_schedule_job_success(self):
        """Test successful job scheduling."""
        job_id = "test_job"
        run_dt = datetime(2025, 1, 22, 15, 30)
        test_func = MagicMock()

        # Mock scheduler to simulate success
        self.scheduler.get_job.return_value = None  # No existing job

        result = self.haunter.schedule_job(
            job_id, run_dt, test_func, "arg1", kwarg1="value1"
        )

        assert result is True
        assert job_id in self.haunter._active_jobs

        # Verify scheduler was called correctly
        self.scheduler.add_job.assert_called_once_with(
            test_func,
            trigger="date",
            run_date=run_dt,
            args=("arg1",),
            kwargs={"kwarg1": "value1"},
            id=job_id,
            replace_existing=True,
        )

    def test_schedule_job_replaces_existing(self):
        """Test that scheduling replaces existing job with same ID."""
        job_id = "test_job"
        run_dt = datetime(2025, 1, 22, 15, 30)
        test_func = MagicMock()

        # Mock existing job
        existing_job = MagicMock()
        self.scheduler.get_job.return_value = existing_job

        result = self.haunter.schedule_job(job_id, run_dt, test_func)

        assert result is True
        # Should call remove_job to cancel existing
        self.scheduler.remove_job.assert_called_once_with(job_id)
        # Should still add new job
        self.scheduler.add_job.assert_called_once()

    def test_cancel_job_existing(self):
        """Test cancelling an existing job."""
        job_id = "test_job"

        # Mock existing job
        existing_job = MagicMock()
        self.scheduler.get_job.return_value = existing_job
        self.haunter._active_jobs.add(job_id)

        result = self.haunter.cancel_job(job_id)

        assert result is True
        assert job_id not in self.haunter._active_jobs
        self.scheduler.remove_job.assert_called_once_with(job_id)

    def test_cancel_job_nonexistent(self):
        """Test cancelling a job that doesn't exist."""
        job_id = "nonexistent_job"

        # Mock no existing job
        self.scheduler.get_job.return_value = None

        result = self.haunter.cancel_job(job_id)

        assert result is False
        self.scheduler.remove_job.assert_not_called()

    def test_cleanup_all_jobs(self):
        """Test cleaning up all active jobs."""
        job_ids = ["job1", "job2", "job3"]

        # Mock existing jobs
        self.scheduler.get_job.return_value = MagicMock()

        # Add jobs to active set
        for job_id in job_ids:
            self.haunter._active_jobs.add(job_id)

        self.haunter.cleanup_all_jobs()

        # All jobs should be removed
        assert len(self.haunter._active_jobs) == 0
        assert self.scheduler.remove_job.call_count == len(job_ids)


class TestBaseHaunterSlack:
    """Test Slack messaging functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.slack = AsyncMock(spec=AsyncWebClient)
        self.scheduler = MagicMock(spec=AsyncIOScheduler)
        self.haunter = TestHaunter(
            session_id=123, slack=self.slack, scheduler=self.scheduler
        )

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """Test successful message sending."""
        # Mock successful response
        self.slack.chat_postMessage.return_value = {"ts": "1234567890.123"}

        result = await self.haunter.send(
            text="Test message", channel="C1234567890", thread_ts="1234567890.000"
        )

        assert result == "1234567890.123"

        self.slack.chat_postMessage.assert_called_once_with(
            channel="C1234567890",
            text="Test message",
            blocks=None,
            thread_ts="1234567890.000",
            username="👻 HaunterBot",
            icon_emoji=":ghost:",
        )

    @pytest.mark.asyncio
    async def test_send_message_failure(self):
        """Test message sending failure."""
        # Mock Slack API failure
        self.slack.chat_postMessage.side_effect = Exception("Slack API error")

        result = await self.haunter.send(text="Test message", channel="C1234567890")

        assert result is None

    @pytest.mark.asyncio
    async def test_schedule_slack_message(self):
        """Test scheduling a Slack message."""
        post_at = datetime(2025, 1, 22, 15, 30)
        expected_unix = int(post_at.timestamp())

        # Mock successful response
        self.slack.chat_scheduleMessage.return_value = {
            "scheduled_message_id": "Q1234567890"
        }

        result = await self.haunter.schedule_slack(
            text="Scheduled message", post_at=post_at, channel="C1234567890"
        )

        assert result == "Q1234567890"

        self.slack.chat_scheduleMessage.assert_called_once_with(
            channel="C1234567890",
            text="Scheduled message",
            post_at=expected_unix,
            thread_ts=None,
        )

    @pytest.mark.asyncio
    async def test_delete_scheduled_message(self):
        """Test deleting a scheduled message."""
        result = await self.haunter.delete_scheduled(
            scheduled_id="Q1234567890", channel="C1234567890"
        )

        assert result is True

        self.slack.chat_deleteScheduledMessage.assert_called_once_with(
            channel="C1234567890", scheduled_message_id="Q1234567890"
        )


class TestBaseHaunterIntentParsing:
    """Test intent parsing functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.slack = AsyncMock(spec=AsyncWebClient)
        self.scheduler = MagicMock(spec=AsyncIOScheduler)
        self.haunter = TestHaunter(
            session_id=123, slack=self.slack, scheduler=self.scheduler
        )

    @pytest.mark.asyncio
    async def test_parse_intent_success(self):
        """Test successful intent parsing."""
        with patch(
            "src.productivity_bot.haunting.base_haunter.AsyncOpenAI"
        ) as mock_openai_class:
            # Mock OpenAI response
            mock_client = AsyncMock()
            mock_openai_class.return_value = mock_client

            mock_response = MagicMock()
            mock_response.choices[0].message.content = (
                '{"action": "postpone", "minutes": 15}'
            )
            mock_client.chat.completions.create.return_value = mock_response

            with patch(
                "src.productivity_bot.haunting.base_haunter.get_config"
            ) as mock_config:
                mock_config.return_value.openai_api_key = "test_key"

                result = await self.haunter.parse_intent("postpone for 15 minutes")

                assert isinstance(result, PlannerAction)
                assert result.action == "postpone"
                assert result.minutes == 15

    @pytest.mark.asyncio
    async def test_parse_intent_failure_fallback(self):
        """Test intent parsing failure returns unknown action."""
        with patch(
            "src.productivity_bot.haunting.base_haunter.AsyncOpenAI"
        ) as mock_openai_class:
            # Mock OpenAI failure
            mock_openai_class.side_effect = Exception("API error")

            with patch("src.productivity_bot.haunting.base_haunter.get_config"):
                result = await self.haunter.parse_intent("invalid input")

                assert isinstance(result, PlannerAction)
                assert result.action == "unknown"


class TestBaseHaunterUtilities:
    """Test utility methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.slack = AsyncMock(spec=AsyncWebClient)
        self.scheduler = MagicMock(spec=AsyncIOScheduler)
        self.haunter = TestHaunter(
            session_id=123, slack=self.slack, scheduler=self.scheduler
        )

    def test_job_id_generation(self):
        """Test consistent job ID generation."""
        job_id = self.haunter._job_id("followup", 2)
        expected = "haunt_123_followup_2"

        assert job_id == expected

    def test_schedule_followup(self):
        """Test scheduling follow-up with back-off."""
        with patch.object(self.haunter, "schedule_job") as mock_schedule:
            mock_schedule.return_value = True

            result = self.haunter._schedule_followup(attempt=1, job_type="reminder")

            assert result is True

            # Verify schedule_job was called with correct parameters
            mock_schedule.assert_called_once()
            call_args = mock_schedule.call_args

            # Check job_id format
            assert call_args[1]["job_id"] == "haunt_123_reminder_2"
            # Check function
            assert call_args[1]["fn"] == self.haunter._send_followup_reminder
            # Check attempt parameter
            assert call_args[1]["attempt"] == 2
