"""
Test for the session manager integration.
"""

from datetime import datetime

import pytest

from productivity_bot.models import PlanStatus
from productivity_bot.session_manager import get_session_registry


@pytest.mark.asyncio
async def test_session_registry_creation():
    """Test session registry can create and manage sessions."""
    registry = get_session_registry()

    # Test creating a session
    session_id, session = await registry.create_planning_session(
        user_id="U12345", event_id="test_event_1", scheduled_for=datetime.now()
    )

    assert session_id is not None
    assert session.user_id == "U12345"
    assert session.event_id == "test_event_1"
    assert session.status == PlanStatus.NOT_STARTED

    # Test session lookup
    session_data = await registry.get_session_by_id(session_id)
    assert session_data is not None
    assert session_data["user_id"] == "U12345"
    assert session_data["event_id"] == "test_event_1"


@pytest.mark.asyncio
async def test_session_thread_linking():
    """Test linking sessions to Slack threads."""
    registry = get_session_registry()

    # Create session without thread info
    session_id, session = await registry.create_planning_session(
        user_id="U12345", event_id="test_event_2", scheduled_for=datetime.now()
    )

    # Update with thread info
    thread_ts = "1234567890.123456"
    channel_id = "C1234567890"

    success = await registry.update_session_thread(session_id, thread_ts, channel_id)
    assert success

    # Verify thread lookup works
    session_data = await registry.get_session_by_thread(thread_ts)
    assert session_data is not None
    assert session_data["session_id"] == session_id
    assert session_data["thread_ts"] == thread_ts
    assert session_data["channel_id"] == channel_id


@pytest.mark.asyncio
async def test_mark_session_done():
    """Test marking sessions as complete."""
    registry = get_session_registry()

    # Create session
    session_id, session = await registry.create_planning_session(
        user_id="U12345",
        event_id="test_event_3",
        thread_ts="1234567890.123457",
        channel_id="C1234567890",
        scheduled_for=datetime.now(),
    )

    # Mark as done (skip emoji for test)
    success = await registry.mark_session_done(session_id, add_emoji=False)
    assert success

    # Verify status updated
    session_data = await registry.get_session_by_id(session_id)
    assert session_data is not None
    assert session_data["status"] == PlanStatus.COMPLETE
    assert session_data.get("completed_at") is not None


def test_session_registry_singleton():
    """Test that get_session_registry returns the same instance."""
    registry1 = get_session_registry()
    registry2 = get_session_registry()

    assert registry1 is registry2
