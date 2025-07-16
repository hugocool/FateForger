"""
Simple integration test to verify haunt_user function implementation.
"""

import pytest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

from productivity_bot.haunter_bot import haunt_user
from productivity_bot.models import PlanningSession, PlanStatus


@pytest.mark.asyncio
async def test_haunt_user_integration():
    """Integration test to verify haunt_user function works end-to-end."""

    # Create a mock session
    mock_session = PlanningSession(
        id=42,
        user_id="U12345",
        date=date.today(),
        scheduled_for=datetime.now(timezone.utc),
        status=PlanStatus.NOT_STARTED,
        slack_scheduled_message_id=None,
        haunt_attempt=0,
        scheduler_job_id=None,
    )

    # Mock all the dependencies
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
        patch(
            "productivity_bot.scheduler.cancel_user_haunt"
        ) as mock_cancel_job,
        patch("slack_bolt.async_app.AsyncApp") as mock_app_class,
        patch("datetime.datetime") as mock_datetime,
    ):

        # Setup mocks
        mock_get_session.return_value = mock_session
        mock_update_session.return_value = True
        mock_schedule_job.return_value = "test_job_123"
        mock_datetime.now.return_value = datetime.now(timezone.utc)

        mock_app = AsyncMock()
        mock_app_class.return_value = mock_app
        mock_app.client.chat_postMessage = AsyncMock(
            return_value={"ok": True}
        )

        # Execute the function
        await haunt_user(42)

        # Verify it completed without errors
        assert mock_get_session.called
        assert mock_app.client.chat_postMessage.called
        assert mock_schedule_job.called
        assert mock_update_session.called

        print("✅ haunt_user integration test passed!")


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_haunt_user_integration())
