"""
Integration tests for calendar sync, Slack cleanup, and agent activation.

Tests the complete flow from calendar events to Slack notifications including:
- Event move/delete detection and scheduler synchronization
- Slack message cleanup when events change or users respond
- Real OpenAI Assistant Agent integration with MCP tools
- End-to-end planning session lifecycle
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from productivity_bot.calendar_watch_server import CalendarWatchServer
from productivity_bot.database import get_db_session
from productivity_bot.models import CalendarEvent, EventStatus, PlanningSession, PlanStatus


@pytest.fixture
async def calendar_server():
    """Create a calendar watch server for testing."""
    return CalendarWatchServer()


@pytest.fixture
async def mock_slack_app():
    """Mock Slack app with chat methods."""
    mock_app = MagicMock()
    mock_app.client = AsyncMock()
    mock_app.client.chat_postMessage = AsyncMock(return_value={"ts": "1234567890.123"})
    mock_app.client.chat_scheduleMessage = AsyncMock(return_value={"scheduled_message_id": "msg_123"})
    mock_app.client.chat_deleteScheduledMessage = AsyncMock()
    return mock_app


@pytest.fixture
async def test_calendar_event():
    """Create a test calendar event."""
    return {
        "id": "test_event_move_123",
        "summary": "Planning Session",
        "start": {"dateTime": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        "end": {"dateTime": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()},
        "status": "confirmed",
        "organizer": {"email": "test@example.com"},
        "attendees": [{"email": "test@example.com", "responseStatus": "accepted"}],
        "location": "Test Room",
        "updated": datetime.now(timezone.utc).isoformat()
    }


@pytest.fixture
async def test_planning_session():
    """Create a test planning session."""
    async with get_db_session() as db:
        session = PlanningSession(
            user_id="test_user_456",
            date=datetime.now(timezone.utc).date(),
            status=PlanStatus.NOT_STARTED,
            scheduled_for=datetime.now(timezone.utc) + timedelta(hours=1),
            event_id="test_event_move_123",
            channel_id="C123456789",
            thread_ts="1234567890.123",
            scheduler_job_id="job_123",
            slack_scheduled_message_id="msg_456"
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        yield session
        
        # Cleanup
        await db.delete(session)
        await db.commit()


class TestCalendarEventMoveIntegration:
    """Test complete event move flow with Slack cleanup and notifications."""
    
    @patch('productivity_bot.calendar_watch_server.get_slack_app')
    @patch('productivity_bot.agents.slack_assistant_agent.get_slack_assistant_agent')
    async def test_event_move_triggers_cleanup_and_notification(
        self, mock_get_agent, mock_get_slack_app, calendar_server, mock_slack_app, 
        test_calendar_event, test_planning_session
    ):
        """Test that moving an event cleans up scheduled messages and sends notification."""
        
        # Setup mocks
        mock_get_slack_app.return_value = mock_slack_app
        mock_agent = AsyncMock()
        mock_agent.process_slack_thread_reply = AsyncMock(return_value=MagicMock(action="postpone"))
        mock_get_agent.return_value = mock_agent
        
        # Simulate event move - change start time
        original_time = datetime.now(timezone.utc) + timedelta(hours=1)
        new_time = datetime.now(timezone.utc) + timedelta(hours=3)
        
        test_calendar_event["start"]["dateTime"] = new_time.isoformat()
        test_calendar_event["end"]["dateTime"] = (new_time + timedelta(hours=1)).isoformat()
        
        # Process the event update
        await calendar_server._upsert_calendar_event(test_calendar_event)
        
        # Verify scheduled message cleanup was called
        mock_slack_app.client.chat_deleteScheduledMessage.assert_called_once_with(
            channel=test_planning_session.user_id,
            scheduled_message_id=test_planning_session.slack_scheduled_message_id
        )
        
        # Verify Slack notification was sent
        mock_slack_app.client.chat_postMessage.assert_called_once()
        call_args = mock_slack_app.client.chat_postMessage.call_args
        assert call_args[1]["channel"] == test_planning_session.channel_id
        assert call_args[1]["thread_ts"] == test_planning_session.thread_ts
        assert "moved" in call_args[1]["text"] or "updated" in call_args[1]["text"]
        
        # Verify planning session was updated
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == test_planning_session.id)
            )
            updated_session = result.scalar_one()
            assert updated_session.scheduled_for.replace(second=0, microsecond=0) == new_time.replace(second=0, microsecond=0)

    @patch('productivity_bot.calendar_watch_server.get_slack_app')
    @patch('productivity_bot.agents.slack_assistant_agent.get_slack_assistant_agent')
    async def test_event_cancellation_marks_session_cancelled(
        self, mock_get_agent, mock_get_slack_app, calendar_server, mock_slack_app, 
        test_calendar_event, test_planning_session
    ):
        """Test that cancelling an event marks session as CANCELLED and continues haunting."""
        
        # Setup mocks
        mock_get_slack_app.return_value = mock_slack_app
        mock_agent = AsyncMock()
        mock_agent.process_slack_thread_reply = AsyncMock(return_value=MagicMock(action="recreate_event"))
        mock_get_agent.return_value = mock_agent
        
        # Simulate event cancellation
        test_calendar_event["status"] = "cancelled"
        
        # Process the event update
        await calendar_server._upsert_calendar_event(test_calendar_event)
        
        # Verify planning session marked as CANCELLED (not COMPLETE)
        async with get_db_session() as db:
            result = await db.execute(
                select(PlanningSession).where(PlanningSession.id == test_planning_session.id)
            )
            updated_session = result.scalar_one()
            assert updated_session.status == PlanStatus.CANCELLED
            assert "cancelled" in updated_session.notes.lower()
            assert "planning still needs to be completed" in updated_session.notes
        
        # Verify Slack notification was sent with cancellation message
        mock_slack_app.client.chat_postMessage.assert_called_once()
        call_args = mock_slack_app.client.chat_postMessage.call_args
        assert "cancelled" in call_args[1]["text"].lower()
        assert "still needs to be completed" in call_args[1]["text"] or "cannot be skipped" in call_args[1]["text"]


class TestSlackMessageCleanup:
    """Test Slack scheduled message cleanup functionality."""
    
    @patch('productivity_bot.haunter_bot.get_slack_app')
    async def test_user_response_cleans_up_scheduled_messages(
        self, mock_get_slack_app, mock_slack_app, test_planning_session
    ):
        """Test that user responses trigger cleanup of scheduled messages."""
        
        mock_get_slack_app.return_value = mock_slack_app
        
        from productivity_bot.haunter_bot import _cleanup_scheduled_slack_message
        
        # Simulate user completing the session
        test_planning_session.status = PlanStatus.COMPLETE
        
        # Cleanup scheduled messages
        await _cleanup_scheduled_slack_message(test_planning_session)
        
        # Verify scheduled message was deleted
        mock_slack_app.client.chat_deleteScheduledMessage.assert_called_once_with(
            channel=test_planning_session.user_id,
            scheduled_message_id=test_planning_session.slack_scheduled_message_id
        )

    @patch('productivity_bot.haunter_bot.get_slack_app')
    async def test_haunter_cleans_up_on_completion(
        self, mock_get_slack_app, mock_slack_app, test_planning_session
    ):
        """Test that haunter bot cleans up scheduled messages when session is completed."""
        
        mock_get_slack_app.return_value = mock_slack_app
        
        from productivity_bot.haunter_bot import haunt_user
        
        # Mark session as complete
        test_planning_session.status = PlanStatus.COMPLETE
        
        with patch('productivity_bot.haunter_bot.PlanningSessionService.get_session_by_id', 
                   return_value=test_planning_session):
            with patch('productivity_bot.haunter_bot.cancel_user_haunt') as mock_cancel:
                await haunt_user(test_planning_session.id)
        
        # Verify cleanup was called
        mock_slack_app.client.chat_deleteScheduledMessage.assert_called_once()
        mock_cancel.assert_called_once_with(test_planning_session.id)


class TestCancelledSessionBehavior:
    """Test cancelled session handling with 24-hour timeout."""
    
    @patch('productivity_bot.haunter_bot.get_slack_app')
    async def test_cancelled_session_continues_haunting(
        self, mock_get_slack_app, mock_slack_app, test_planning_session
    ):
        """Test that CANCELLED sessions continue to be haunted (not stopped)."""
        
        mock_get_slack_app.return_value = mock_slack_app
        
        from productivity_bot.haunter_bot import haunt_user
        
        # Mark session as cancelled
        test_planning_session.status = PlanStatus.CANCELLED
        test_planning_session.haunt_attempt = 0
        
        with patch('productivity_bot.haunter_bot.PlanningSessionService.get_session_by_id', 
                   return_value=test_planning_session):
            with patch('productivity_bot.haunter_bot.PlanningSessionService.update_session') as mock_update:
                with patch('productivity_bot.haunter_bot.schedule_user_haunt', return_value="job_new"):
                    await haunt_user(test_planning_session.id)
        
        # Verify haunting continued (session updated with new attempt)
        mock_update.assert_called_once()
        updated_session = mock_update.call_args[0][0]
        assert updated_session.haunt_attempt == 1
        
        # Verify cancellation-specific message was sent
        mock_slack_app.client.chat_postMessage.assert_called_once()
        message_text = mock_slack_app.client.chat_postMessage.call_args[1]["text"]
        assert "cancelled" in message_text.lower()
        assert "still needs to be done" in message_text.lower()

    @patch('productivity_bot.haunter_bot.get_slack_app')
    async def test_cancelled_session_24h_timeout(
        self, mock_get_slack_app, mock_slack_app, test_planning_session
    ):
        """Test that CANCELLED sessions stop haunting after 24 hours but keep status."""
        
        mock_get_slack_app.return_value = mock_slack_app
        
        from productivity_bot.haunter_bot import haunt_user
        
        # Mark session as cancelled and old
        test_planning_session.status = PlanStatus.CANCELLED
        test_planning_session.created_at = datetime.now(timezone.utc) - timedelta(hours=25)  # >24h old
        
        with patch('productivity_bot.haunter_bot.PlanningSessionService.get_session_by_id', 
                   return_value=test_planning_session):
            with patch('productivity_bot.haunter_bot.cancel_user_haunt') as mock_cancel:
                await haunt_user(test_planning_session.id)
        
        # Verify haunting was cancelled (timeout reached)
        mock_cancel.assert_called_once_with(test_planning_session.id)
        
        # Verify no new Slack message was sent
        mock_slack_app.client.chat_postMessage.assert_not_called()
        
        # Session should retain CANCELLED status for review
        assert test_planning_session.status == PlanStatus.CANCELLED


class TestOpenAIAgentIntegration:
    """Test OpenAI Assistant Agent integration with MCP tools."""
    
    @patch('productivity_bot.agents.slack_assistant_agent.AsyncOpenAI')
    @patch('productivity_bot.agents.slack_assistant_agent.McpWorkbench')
    async def test_agent_initialization_with_mcp_tools(
        self, mock_workbench_class, mock_openai_class
    ):
        """Test that OpenAI Assistant Agent initializes with MCP calendar tools."""
        
        # Mock MCP workbench
        mock_workbench = AsyncMock()
        mock_workbench.list_tools = AsyncMock(return_value=[
            {"name": "calendar_list_events"},
            {"name": "calendar_create_event"},
            {"name": "calendar_update_event"}
        ])
        mock_workbench.__aenter__ = AsyncMock(return_value=mock_workbench)
        mock_workbench_class.return_value = mock_workbench
        
        # Mock OpenAI client
        mock_client = AsyncMock()
        mock_openai_class.return_value = mock_client
        
        from productivity_bot.agents.slack_assistant_agent import SlackAssistantAgent
        
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'test_key'}):
            agent = SlackAssistantAgent()
            await agent._initialize_agent()
        
        # Verify MCP workbench was initialized
        mock_workbench_class.assert_called_once()
        mock_workbench.list_tools.assert_called_once()
        
        # Verify agent was created with tools
        assert agent._initialized
        assert agent.agent is not None

    async def test_agent_fallback_without_openai_key(self):
        """Test agent gracefully falls back when OpenAI key is missing."""
        
        from productivity_bot.agents.slack_assistant_agent import SlackAssistantAgent
        
        with patch.dict('os.environ', {}, clear=True):  # No OPENAI_API_KEY
            agent = SlackAssistantAgent()
            await agent._initialize_agent()
        
        # Should initialize with mock agent
        assert agent._initialized
        assert agent.agent is None  # Mock mode

    async def test_agent_processes_cancellation_context(self):
        """Test agent processes cancellation context correctly."""
        
        from productivity_bot.agents.slack_assistant_agent import SlackAssistantAgent
        
        agent = SlackAssistantAgent()
        agent._initialized = True
        agent.agent = None  # Mock mode
        
        # Test cancellation message
        result = await agent.process_slack_thread_reply(
            "I cancelled my planning event",
            session_context={"type": "cancellation", "event": {"title": "Planning Session"}}
        )
        
        assert result.action == "recreate_event"

    async def test_agent_processes_move_context(self):
        """Test agent processes move context correctly."""
        
        from productivity_bot.agents.slack_assistant_agent import SlackAssistantAgent
        
        agent = SlackAssistantAgent()
        agent._initialized = True
        agent.agent = None  # Mock mode
        
        # Test move message
        result = await agent.process_slack_thread_reply(
            "I moved my planning event to 3pm",
            session_context={"type": "move", "event": {"title": "Planning Session"}}
        )
        
        assert result.action == "postpone"
        assert result.minutes == 60  # Default postpone time


@pytest.mark.integration
async def test_end_to_end_calendar_sync_flow():
    """Integration test for complete calendar sync and notification flow."""
    
    with patch('productivity_bot.calendar_watch_server.get_slack_app') as mock_get_slack_app:
        with patch('productivity_bot.agents.slack_assistant_agent.get_slack_assistant_agent') as mock_get_agent:
            
            # Setup mocks
            mock_slack_app = AsyncMock()
            mock_slack_app.client.chat_postMessage = AsyncMock()
            mock_slack_app.client.chat_deleteScheduledMessage = AsyncMock()
            mock_get_slack_app.return_value = mock_slack_app
            
            mock_agent = AsyncMock()
            mock_agent.process_slack_thread_reply = AsyncMock(return_value=MagicMock(action="recreate_event"))
            mock_get_agent.return_value = mock_agent
            
            # Create test data
            calendar_server = CalendarWatchServer()
            
            # Test complete flow: event creation → cancellation → notification
            event_data = {
                "id": "e2e_test_event",
                "summary": "E2E Planning Session",
                "start": {"dateTime": datetime.now(timezone.utc).isoformat()},
                "end": {"dateTime": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
                "status": "confirmed",
                "organizer": {"email": "test@example.com"},
                "attendees": [{"email": "test@example.com"}]
            }
            
            # Step 1: Create event
            await calendar_server._upsert_calendar_event(event_data)
            
            # Step 2: Create linked planning session
            async with get_db_session() as db:
                session = PlanningSession(
                    user_id="e2e_test_user",
                    date=datetime.now(timezone.utc).date(),
                    status=PlanStatus.NOT_STARTED,
                    scheduled_for=datetime.now(timezone.utc),
                    event_id="e2e_test_event",
                    channel_id="C_E2E_TEST",
                    thread_ts="1234567890.999"
                )
                db.add(session)
                await db.commit()
                await db.refresh(session)
                session_id = session.id
            
            # Step 3: Cancel event
            event_data["status"] = "cancelled"
            await calendar_server._upsert_calendar_event(event_data)
            
            # Step 4: Verify results
            async with get_db_session() as db:
                result = await db.execute(
                    select(PlanningSession).where(PlanningSession.id == session_id)
                )
                final_session = result.scalar_one()
                
                # Session should be CANCELLED, not COMPLETE
                assert final_session.status == PlanStatus.CANCELLED
                
                # Slack notification should have been sent
                mock_slack_app.client.chat_postMessage.assert_called()
                
                # Agent should have been invoked for cancellation
                mock_agent.process_slack_thread_reply.assert_called()
                
            # Cleanup
            async with get_db_session() as db:
                await db.delete(final_session)
                await db.commit()


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "--tb=short"])
