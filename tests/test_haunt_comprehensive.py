"""
Comprehensive test demonstrating haunt_user implementation according to spec.

This test validates:
1. Session COMPLETE - cancels APScheduler jobs and Slack scheduled messages
2. First haunt - schedules Slack message and next job with backoff
3. Subsequent haunt escalation - proper backoff timing and escalated messaging
"""

import asyncio
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from productivity_bot.haunter_bot import haunt_user
from productivity_bot.models import PlanningSession, PlanStatus
from productivity_bot.common import backoff_minutes


async def test_complete_session_cancellation():
    """Test 1: Session COMPLETE cancels jobs and scheduled messages."""
    print("🧪 Test 1: Session COMPLETE cancellation")

    # Create a COMPLETE session with existing scheduled message and job
    complete_session = PlanningSession(
        id=1,
        user_id="U12345",
        date=date.today(),
        scheduled_for=datetime.now(timezone.utc),
        status=PlanStatus.COMPLETE,
        slack_scheduled_message_id="SM123456",
        haunt_attempt=2,
        scheduler_job_id="job_123",
    )

    with (
        patch(
            "productivity_bot.database.PlanningSessionService.get_session_by_id"
        ) as mock_get_session,
        patch(
            "productivity_bot.scheduler.cancel_planning_session_haunt"
        ) as mock_cancel_job,
        patch("productivity_bot.common.get_slack_app") as mock_get_app,
    ):

        # Setup mocks
        mock_get_session.return_value = complete_session

        mock_app = AsyncMock()
        mock_get_app.return_value = mock_app
        mock_app.client.chat_deleteScheduledMessage = AsyncMock()

        # Execute the function
        await haunt_user(1)

        # Verify cancellation logic
        mock_cancel_job.assert_called_once_with(1)
        mock_app.client.chat_deleteScheduledMessage.assert_called_once_with(
            channel="U12345", scheduled_message_id="SM123456"
        )

        print("✅ Test 1 PASSED: APScheduler job and Slack message cancelled")


async def test_first_haunt_attempt():
    """Test 2: First haunt schedules message and next job."""
    print("\n🧪 Test 2: First haunt attempt")

    # Create a session with haunt_attempt=0 (first attempt)
    first_session = PlanningSession(
        id=2,
        user_id="U67890",
        date=date.today(),
        scheduled_for=datetime.now(timezone.utc),
        status=PlanStatus.IN_PROGRESS,
        slack_scheduled_message_id=None,
        haunt_attempt=0,
        scheduler_job_id=None,
    )

    mock_now = datetime.now(timezone.utc)

    with (
        patch(
            "productivity_bot.database.PlanningSessionService.get_session_by_id"
        ) as mock_get_session,
        patch(
            "productivity_bot.database.PlanningSessionService.update_session"
        ) as mock_update_session,
        patch(
            "productivity_bot.scheduler.schedule_planning_session_haunt"
        ) as mock_schedule_job,
        patch(
            "productivity_bot.scheduler.cancel_planning_session_haunt"
        ) as mock_cancel_job,
        patch("productivity_bot.common.get_slack_app") as mock_get_app,
        patch("datetime.datetime") as mock_datetime,
    ):

        # Setup mocks
        mock_get_session.return_value = first_session
        mock_update_session.return_value = True
        mock_schedule_job.return_value = "new_job_456"
        mock_datetime.now.return_value = mock_now

        mock_app = AsyncMock()
        mock_get_app.return_value = mock_app
        mock_app.client.chat_scheduleMessage = AsyncMock(
            return_value={"scheduled_message_id": "SM789123"}
        )

        # Execute the function
        await haunt_user(2)

        # Verify Slack message scheduling
        mock_app.client.chat_scheduleMessage.assert_called_once_with(
            channel="U67890",
            text="⏰ It's time to plan your day! Please open your planning session.",
            post_at=int(mock_now.timestamp()),
        )

        # Verify session updates
        assert first_session.slack_scheduled_message_id == "SM789123"
        assert first_session.haunt_attempt == 1
        assert first_session.scheduler_job_id == "new_job_456"

        # Verify APScheduler job management
        mock_cancel_job.assert_called_once_with(2)
        mock_schedule_job.assert_called_once()
        mock_update_session.assert_called_once_with(first_session)

        print("✅ Test 2 PASSED: First haunt scheduled message and next job")


async def test_subsequent_haunt_escalation():
    """Test 3: Subsequent haunt with escalation and proper backoff."""
    print("\n🧪 Test 3: Subsequent haunt escalation")

    # Create a session with haunt_attempt=1 (second attempt)
    escalation_session = PlanningSession(
        id=3,
        user_id="U54321",
        date=date.today(),
        scheduled_for=datetime.now(timezone.utc),
        status=PlanStatus.NOT_STARTED,
        slack_scheduled_message_id="SM999888",
        haunt_attempt=1,
        scheduler_job_id="job_456",
    )

    mock_now = datetime.now(timezone.utc)

    with (
        patch(
            "productivity_bot.database.PlanningSessionService.get_session_by_id"
        ) as mock_get_session,
        patch(
            "productivity_bot.database.PlanningSessionService.update_session"
        ) as mock_update_session,
        patch(
            "productivity_bot.scheduler.schedule_planning_session_haunt"
        ) as mock_schedule_job,
        patch(
            "productivity_bot.scheduler.cancel_planning_session_haunt"
        ) as mock_cancel_job,
        patch("productivity_bot.common.get_slack_app") as mock_get_app,
        patch("datetime.datetime") as mock_datetime,
    ):

        # Setup mocks
        mock_get_session.return_value = escalation_session
        mock_update_session.return_value = True
        mock_schedule_job.return_value = "escalated_job_789"
        mock_datetime.now.return_value = mock_now

        mock_app = AsyncMock()
        mock_get_app.return_value = mock_app
        mock_app.client.chat_scheduleMessage = AsyncMock(
            return_value={"scheduled_message_id": "SM999000"}
        )

        # Execute the function
        await haunt_user(3)

        # Verify escalated message text
        mock_app.client.chat_scheduleMessage.assert_called_once_with(
            channel="U54321",
            text="⏰ Reminder 2: don't forget to plan tomorrow's schedule!",
            post_at=int(mock_now.timestamp()),
        )

        # Verify updated session fields
        assert escalation_session.slack_scheduled_message_id == "SM999000"
        assert escalation_session.haunt_attempt == 2
        assert escalation_session.scheduler_job_id == "escalated_job_789"

        # Verify scheduling with proper backoff delay (attempt 2 -> 20 minutes)
        expected_delay = backoff_minutes(2)
        assert expected_delay == 20, f"Expected 20 minute delay, got {expected_delay}"

        # Verify the next job is scheduled ~20 minutes in the future
        schedule_call_args = mock_schedule_job.call_args
        scheduled_session_id, scheduled_time = schedule_call_args[0]
        assert scheduled_session_id == 3

        # Allow some tolerance for timing (within 1 minute)
        expected_time = mock_now + timedelta(minutes=20)
        time_diff = abs((scheduled_time - expected_time).total_seconds())
        assert (
            time_diff < 60
        ), f"Scheduled time {scheduled_time} not within expected range of {expected_time}"

        print("✅ Test 3 PASSED: Escalated message with proper 20-minute backoff")


async def test_backoff_progression():
    """Test 4: Verify backoff progression matches spec."""
    print("\n🧪 Test 4: Backoff progression verification")

    # Test the backoff progression as specified
    delays = [
        (0, 5),  # Initial delay: 5 minutes
        (1, 10),  # Second attempt: 10 minutes
        (2, 20),  # Third attempt: 20 minutes
        (3, 40),  # Fourth attempt: 40 minutes
        (4, 60),  # Capped at 60 minutes
        (10, 60),  # Still capped at 60 minutes
    ]

    for attempt, expected_delay in delays:
        actual_delay = backoff_minutes(attempt)
        assert (
            actual_delay == expected_delay
        ), f"Attempt {attempt}: expected {expected_delay} minutes, got {actual_delay}"
        print(f"  ✓ Attempt {attempt}: {actual_delay} minutes")

    print("✅ Test 4 PASSED: Backoff progression 5→10→20→40→60(capped)")


async def main():
    """Run all tests to demonstrate haunt_user implementation."""
    print("🚀 Running comprehensive haunt_user implementation tests...\n")

    await test_complete_session_cancellation()
    await test_first_haunt_attempt()
    await test_subsequent_haunt_escalation()
    await test_backoff_progression()

    print("\n🎉 ALL HAUNT_USER TESTS PASSED!")
    print("\nImplementation Summary:")
    print("✅ Session COMPLETE → cancels APScheduler jobs & Slack messages")
    print("✅ First haunt → schedules immediate Slack message + next job")
    print("✅ Subsequent haunts → escalated messaging with exponential backoff")
    print("✅ Backoff progression → 5→10→20→40→60(capped) minutes")
    print(
        "✅ Slack scheduled messages → immediate post with scheduled_message_id tracking"
    )
    print("✅ APScheduler integration → job cancellation and rescheduling")
    print(
        "✅ Database persistence → slack_scheduled_message_id and haunt_attempt fields"
    )


if __name__ == "__main__":
    asyncio.run(main())
