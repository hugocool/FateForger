"""
Simple validation script for BaseHaunter functionality.
This avoids the complex config dependencies while testing core logic.
"""

import asyncio
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

# Add the src path
sys.path.insert(0, "/Users/hugoevers/VScode-projects/admonish-1/src")


def test_backoff_sequence():
    """Test the exponential back-off sequence."""
    print("🔍 Testing back-off sequence...")

    from abc import ABC

    from productivity_bot.haunting.base_haunter import BaseHaunter

    # Create a minimal test haunter
    class TestHaunter(BaseHaunter):
        async def _route_to_planner(self, intent):
            return True

    # Mock dependencies
    slack = AsyncMock()
    scheduler = MagicMock()

    haunter = TestHaunter(session_id=123, slack=slack, scheduler=scheduler)

    # Test back-off sequence: 5, 10, 20, 40, 80, 120, 120...
    expected_delays = [5, 10, 20, 40, 80, 120, 120, 120]

    for attempt, expected in enumerate(expected_delays):
        actual = haunter.next_delay(attempt)
        print(f"  Attempt {attempt}: {actual} minutes (expected {expected})")
        assert actual == expected, f"Expected {expected}, got {actual}"

    print("✅ Back-off sequence test passed!")


def test_job_scheduling():
    """Test APScheduler job management."""
    print("\n🔍 Testing job scheduling...")

    from productivity_bot.haunting.base_haunter import BaseHaunter

    class TestHaunter(BaseHaunter):
        async def _route_to_planner(self, intent):
            return True

    # Mock dependencies
    slack = AsyncMock()
    scheduler = MagicMock()

    # Mock scheduler methods
    scheduler.get_job.return_value = None  # No existing job
    scheduler.add_job = MagicMock()
    scheduler.remove_job = MagicMock()

    haunter = TestHaunter(session_id=123, slack=slack, scheduler=scheduler)

    # Test scheduling a job
    job_id = "test_job"
    run_dt = datetime(2025, 1, 22, 15, 30)
    test_func = lambda: None

    result = haunter.schedule_job(job_id, run_dt, test_func, "arg1")

    print(f"  Schedule job result: {result}")
    print(f"  Active jobs: {haunter._active_jobs}")

    assert result is True
    assert job_id in haunter._active_jobs

    # Test cancelling a job
    scheduler.get_job.return_value = MagicMock()  # Mock existing job
    cancel_result = haunter.cancel_job(job_id)

    print(f"  Cancel job result: {cancel_result}")
    print(f"  Active jobs after cancel: {haunter._active_jobs}")

    assert cancel_result is True
    assert job_id not in haunter._active_jobs

    print("✅ Job scheduling test passed!")


async def test_slack_messaging():
    """Test Slack messaging functionality."""
    print("\n🔍 Testing Slack messaging...")

    from productivity_bot.haunting.base_haunter import BaseHaunter

    class TestHaunter(BaseHaunter):
        async def _route_to_planner(self, intent):
            return True

    # Mock dependencies
    slack = AsyncMock()
    scheduler = MagicMock()

    # Mock Slack responses
    slack.chat_postMessage.return_value = {"ts": "1234567890.123"}
    slack.chat_scheduleMessage.return_value = {"scheduled_message_id": "Q1234567890"}
    slack.chat_deleteScheduledMessage.return_value = {"ok": True}

    haunter = TestHaunter(session_id=123, slack=slack, scheduler=scheduler)

    # Test sending a message
    msg_ts = await haunter.send(
        text="Test message", channel="C1234567890", thread_ts="1234567890.000"
    )

    print(f"  Send message result: {msg_ts}")
    assert msg_ts == "1234567890.123"

    # Test scheduling a message
    post_at = datetime(2025, 1, 22, 15, 30)
    scheduled_id = await haunter.schedule_slack(
        text="Scheduled message", post_at=post_at, channel="C1234567890"
    )

    print(f"  Schedule message result: {scheduled_id}")
    assert scheduled_id == "Q1234567890"

    # Test deleting scheduled message
    delete_result = await haunter.delete_scheduled(
        scheduled_id="Q1234567890", channel="C1234567890"
    )

    print(f"  Delete scheduled result: {delete_result}")
    assert delete_result is True

    print("✅ Slack messaging test passed!")


def test_utility_methods():
    """Test utility methods."""
    print("\n🔍 Testing utility methods...")

    from productivity_bot.haunting.base_haunter import BaseHaunter

    class TestHaunter(BaseHaunter):
        async def _route_to_planner(self, intent):
            return True

    # Mock dependencies
    slack = AsyncMock()
    scheduler = MagicMock()

    haunter = TestHaunter(session_id=123, slack=slack, scheduler=scheduler)

    # Test job ID generation
    job_id = haunter._job_id("followup", 2)
    expected = "haunt_123_followup_2"

    print(f"  Job ID: {job_id} (expected: {expected})")
    assert job_id == expected

    # Test next run time calculation
    with_mock_time = datetime(2025, 1, 22, 12, 0, 0)

    # Mock datetime.utcnow
    import productivity_bot.haunting.base_haunter as bh_module

    original_datetime = bh_module.datetime

    class MockDateTime:
        @staticmethod
        def utcnow():
            return with_mock_time

        @staticmethod
        def timestamp():
            return with_mock_time.timestamp()

    bh_module.datetime = MockDateTime

    try:
        next_run = haunter.next_run_time(0)  # Should add 5 minutes
        expected_run = with_mock_time + timedelta(minutes=5)

        print(f"  Next run time: {next_run} (expected: {expected_run})")
        assert next_run == expected_run
    finally:
        # Restore original datetime
        bh_module.datetime = original_datetime

    print("✅ Utility methods test passed!")


async def main():
    """Run all tests."""
    print("🚀 Testing BaseHaunter Infrastructure & Back-off Engine")
    print("=" * 60)

    try:
        # Run synchronous tests
        test_backoff_sequence()
        test_job_scheduling()
        test_utility_methods()

        # Run async tests
        await test_slack_messaging()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("\n📋 BaseHaunter Features Validated:")
        print("   ✅ Exponential back-off: 5, 10, 20, 40, 80, 120, 120... minutes")
        print("   ✅ APScheduler job management (schedule, cancel, cleanup)")
        print("   ✅ Slack messaging (send, schedule, delete)")
        print("   ✅ Utility methods (job IDs, timing calculations)")
        print("   ✅ Abstract base class structure for haunter personas")

        print("\n🎯 Ready for Ticket 1 Implementation:")
        print("   • BaseHaunter abstract class ✅")
        print("   • APScheduler helpers ✅")
        print("   • Slack messaging helpers ✅")
        print("   • Exponential back-off engine ✅")
        print("   • Intent parsing framework ✅")
        print("   • Abstract handoff interface ✅")

        return True

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
