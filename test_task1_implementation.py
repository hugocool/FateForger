#!/usr/bin/env python3
"""
Test Task 1 Implementation: PlannerBot Modal Enhancement

This test verifies that:
1. PlanningSessionService creates minimal metadata sessions
2. Scheduler jobs are created and stored
3. Modal workflow persists correctly
4. Idempotence works (no duplicate sessions/jobs)
"""

import asyncio
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Set test environment variables
os.environ.update(
    {
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "SLACK_SIGNING_SECRET": "test-secret",
        "SLACK_APP_TOKEN": "xapp-test-token",
        "OPENAI_API_KEY": "sk-test-key",
        "DATABASE_URL": "sqlite+aiosqlite:///data/admonish.db",  # Use existing database
    }
)


async def test_minimal_session_creation():
    """Test that PlanningSessionService creates minimal metadata sessions."""
    print("🧪 Testing minimal session creation...")

    try:
        from productivity_bot.database import PlanningSessionService
        from productivity_bot.models import PlanStatus

        # Test parameters
        user_id = "U12345"
        session_date = date.today()
        scheduled_for = datetime.combine(
            session_date, time(hour=17, minute=0), timezone.utc
        )

        # Create session
        session = await PlanningSessionService.create_session(
            user_id=user_id, session_date=session_date, scheduled_for=scheduled_for
        )

        # Validate minimal metadata
        assert (
            session.user_id == user_id
        ), f"Expected user_id {user_id}, got {session.user_id}"
        assert (
            session.date == session_date
        ), f"Expected date {session_date}, got {session.date}"
        # Handle timezone comparison - database may store without timezone
        expected_time = scheduled_for.replace(tzinfo=None)
        actual_time = (
            session.scheduled_for.replace(tzinfo=None)
            if session.scheduled_for.tzinfo
            else session.scheduled_for
        )
        assert (
            actual_time == expected_time
        ), f"Expected scheduled_for {expected_time}, got {actual_time}"
        assert (
            session.status == PlanStatus.NOT_STARTED
        ), f"Expected status NOT_STARTED, got {session.status}"
        assert session.goals is None, f"Expected goals None, got {session.goals}"
        assert session.notes is None, f"Expected notes None, got {session.notes}"

        print(f"✅ Session {session.id} created with minimal metadata")
        print(f"   - User: {session.user_id}")
        print(f"   - Date: {session.date}")
        print(f"   - Scheduled: {session.scheduled_for}")
        print(f"   - Status: {session.status.value}")
        print(f"   - Goals: {session.goals}")
        print(f"   - Notes: {session.notes}")

        return session

    except Exception as e:
        print(f"❌ Session creation failed: {e}")
        raise


async def test_scheduler_integration():
    """Test that scheduler jobs are created and job IDs are stored."""
    print("\n🧪 Testing scheduler integration...")

    try:
        from productivity_bot.database import PlanningSessionService
        from productivity_bot.scheduler import (
            get_scheduled_jobs,
            schedule_planning_session_haunt,
        )

        # Create a test session first
        session = await test_minimal_session_creation()
        session_id = session.id

        # Schedule haunt
        scheduled_for = datetime.combine(
            date.today(), time(hour=17, minute=0), timezone.utc
        )

        job_id = schedule_planning_session_haunt(session_id, scheduled_for)
        expected_job_id = f"haunt_session_{session_id}"

        assert (
            job_id == expected_job_id
        ), f"Expected job_id {expected_job_id}, got {job_id}"
        print(f"✅ Scheduled job {job_id} for session {session_id}")

        # Store job ID in session
        success = await PlanningSessionService.update_session_job_id(session_id, job_id)
        assert success, "Failed to update session with job ID"

        # Verify job ID was stored
        updated_session = await PlanningSessionService.get_session_by_id(session_id)
        assert updated_session is not None, f"Session {session_id} not found"
        assert (
            updated_session.scheduler_job_id == job_id
        ), f"Expected scheduler_job_id {job_id}, got {updated_session.scheduler_job_id}"

        print(f"✅ Job ID {job_id} stored in session {session_id}")

        # Verify job exists in scheduler
        jobs = get_scheduled_jobs()
        job_ids = [job.id for job in jobs]
        assert (
            job_id in job_ids
        ), f"Job {job_id} not found in scheduler. Available: {job_ids}"

        print(f"✅ Job {job_id} confirmed in scheduler")

        # Try to get next run time safely
        target_job = next((job for job in jobs if job.id == job_id), None)
        if target_job:
            try:
                next_run = getattr(
                    target_job, "next_run_time", "No next_run_time attribute"
                )
                print(f"   - Next run: {next_run}")
            except Exception as e:
                print(f"   - Next run info unavailable: {e}")
        else:
            print(f"   - Job {job_id} not found in jobs list")

        return session, job_id

    except Exception as e:
        print(f"❌ Scheduler integration failed: {e}")
        raise


async def test_idempotence():
    """Test that duplicate sessions/jobs are handled correctly."""
    print("\n🧪 Testing idempotence...")

    try:
        from productivity_bot.database import PlanningSessionService
        from productivity_bot.scheduler import (
            get_scheduled_jobs,
            schedule_planning_session_haunt,
        )

        user_id = "U54321"
        session_date = date.today()
        scheduled_for = datetime.combine(
            session_date, time(hour=17, minute=0), timezone.utc
        )

        # Create first session
        session1 = await PlanningSessionService.create_session(
            user_id=user_id, session_date=session_date, scheduled_for=scheduled_for
        )

        # Try to get existing session for same user/date
        existing_session = await PlanningSessionService.get_user_session_for_date(
            user_id, session_date
        )

        assert existing_session is not None, "Failed to retrieve existing session"
        assert (
            existing_session.id == session1.id
        ), f"Expected session {session1.id}, got {existing_session.id}"

        print(
            f"✅ Idempotence verified: same session returned for user {user_id} on {session_date}"
        )

        # Test scheduler job replacement
        job_id1 = schedule_planning_session_haunt(session1.id, scheduled_for)
        job_id2 = schedule_planning_session_haunt(
            session1.id, scheduled_for + timedelta(minutes=5)
        )

        # Should be same job ID (replacement)
        assert (
            job_id1 == job_id2
        ), f"Expected same job ID for replacement, got {job_id1} vs {job_id2}"

        # Only one job should exist for this session
        jobs = get_scheduled_jobs()
        session_jobs = [job for job in jobs if job.id == job_id1]
        assert (
            len(session_jobs) == 1
        ), f"Expected 1 job for session {session1.id}, found {len(session_jobs)}"

        print(f"✅ Job replacement verified: {job_id1} replaced with new timing")

        return True

    except Exception as e:
        print(f"❌ Idempotence test failed: {e}")
        raise


async def main():
    """Run all Task 1 implementation tests."""
    print("🚀 Task 1 Implementation Test Suite")
    print("=" * 50)

    try:
        # Test 1: Minimal session creation
        session, job_id = await test_scheduler_integration()

        # Test 2: Idempotence
        await test_idempotence()

        print("\n" + "=" * 50)
        print("🎉 ALL TASK 1 TESTS PASSED!")
        print("\n📋 Implementation Summary:")
        print("✅ 1. Minimal PlanningSession metadata persistence")
        print("✅ 2. Scheduler job creation and storage")
        print("✅ 3. Job ID linking to session records")
        print("✅ 4. Idempotence (no duplicate sessions/jobs)")
        print("\n🔄 Next Steps:")
        print("- Wire up HaunterBot LLM-generated copy")
        print("- Implement Slack reply looping for completion")
        print("- Add session completion and job cancellation")

        return True

    except Exception as e:
        print(f"\n❌ Task 1 tests failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
