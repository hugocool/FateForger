"""
Comprehensive tests for structured intent parsing and Slack routing.

This test suite covers:
1. Structured LLM intent parsing with PlannerAction validation
2. Slack router event handling and action execution
3. Database session lookup and integration
4. Error handling and fallback scenarios
5. End-to-end workflow validation
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from productivity_bot.actions.planner_action import PlannerAction
from productivity_bot.agents.planner_agent import (
    send_to_planner_intent,
    test_planner_agent,
)
from productivity_bot.models import PlanningSession, PlanStatus
from productivity_bot.slack_router import SlackRouter


class TestStructuredIntentParsing:
    """Test structured LLM intent parsing with OpenAI Structured Outputs."""

    @pytest.mark.asyncio
    async def test_postpone_intent_with_minutes(self):
        """Test parsing postpone intent with explicit minutes."""
        with patch(
            "productivity_bot.agents.planner_agent.get_openai_client"
        ) as mock_client:
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.parsed = PlannerAction(
                action="postpone", minutes=30
            )

            mock_client.return_value.beta.chat.completions.parse = AsyncMock(
                return_value=mock_response
            )

            result = await send_to_planner_intent("postpone 30")

            assert result.action == "postpone"
            assert result.minutes == 30
            assert result.is_postpone

    @pytest.mark.asyncio
    async def test_mark_done_intent(self):
        """Test parsing mark done intent."""
        with patch(
            "productivity_bot.agents.planner_agent.get_openai_client"
        ) as mock_client:
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.parsed = PlannerAction(
                action="mark_done", minutes=None
            )

            mock_client.return_value.beta.chat.completions.parse = AsyncMock(
                return_value=mock_response
            )

            result = await send_to_planner_intent("done")

            assert result.action == "mark_done"
            assert result.minutes is None
            assert result.is_mark_done

    @pytest.mark.asyncio
    async def test_recreate_event_intent(self):
        """Test parsing recreate event intent."""
        with patch(
            "productivity_bot.agents.planner_agent.get_openai_client"
        ) as mock_client:
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.parsed = PlannerAction(
                action="recreate_event", minutes=None
            )

            mock_client.return_value.beta.chat.completions.parse = AsyncMock(
                return_value=mock_response
            )

            result = await send_to_planner_intent("recreate event")

            assert result.action == "recreate_event"
            assert result.minutes is None
            assert result.is_recreate_event

    @pytest.mark.asyncio
    async def test_llm_parsing_error_fallback(self):
        """Test fallback behavior when LLM parsing fails."""
        with patch(
            "productivity_bot.agents.planner_agent.get_openai_client"
        ) as mock_client:
            mock_client.return_value.beta.chat.completions.parse = AsyncMock(
                side_effect=Exception("API error")
            )

            result = await send_to_planner_intent("some unclear text")

            # Should fallback to safe default
            assert result.action == "mark_done"
            assert result.minutes is None

    @pytest.mark.asyncio
    async def test_none_response_handling(self):
        """Test handling when OpenAI returns None for parsed response."""
        with patch(
            "productivity_bot.agents.planner_agent.get_openai_client"
        ) as mock_client:
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.parsed = None

            mock_client.return_value.beta.chat.completions.parse = AsyncMock(
                return_value=mock_response
            )

            result = await send_to_planner_intent("unclear input")

            # Should handle None gracefully
            assert result.action == "mark_done"
            assert result.minutes is None

    @pytest.mark.asyncio
    async def test_planner_agent_integration(self):
        """Test the planner agent test function."""
        with patch(
            "productivity_bot.agents.planner_agent.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.return_value = PlannerAction(action="postpone", minutes=10)

            result = await test_planner_agent()

            assert result is True
            mock_intent.assert_called_once_with("postpone 10")


class TestPlannerActionModel:
    """Test the PlannerAction Pydantic model validation."""

    def test_valid_postpone_action(self):
        """Test creating valid postpone action."""
        action = PlannerAction(action="postpone", minutes=15)
        assert action.action == "postpone"
        assert action.minutes == 15
        assert action.is_postpone

    def test_valid_mark_done_action(self):
        """Test creating valid mark done action."""
        action = PlannerAction(action="mark_done", minutes=None)
        assert action.action == "mark_done"
        assert action.minutes is None
        assert action.is_mark_done

    def test_valid_recreate_event_action(self):
        """Test creating valid recreate event action."""
        action = PlannerAction(action="recreate_event", minutes=None)
        assert action.action == "recreate_event"
        assert action.minutes is None
        assert action.is_recreate_event

    def test_postpone_default_minutes(self):
        """Test postpone action with default minutes."""
        action = PlannerAction(action="postpone")
        assert action.get_postpone_minutes() == 15  # Default

    def test_postpone_explicit_minutes(self):
        """Test postpone action with explicit minutes."""
        action = PlannerAction(action="postpone", minutes=30)
        assert action.get_postpone_minutes() == 30

    def test_non_postpone_minutes(self):
        """Test get_postpone_minutes on non-postpone action."""
        action = PlannerAction(action="mark_done")
        assert action.get_postpone_minutes() == 0

    def test_invalid_action_validation(self):
        """Test that invalid actions are rejected."""
        with pytest.raises(ValueError):
            PlannerAction(action="unknown")


class TestSlackRouter:
    """Test the new Slack router implementation."""

    @pytest.fixture
    def mock_app(self):
        """Create a mock Slack app."""
        return Mock()

    @pytest.fixture
    def router(self, mock_app):
        """Create a SlackRouter instance with mocked app."""
        with patch.object(SlackRouter, "_register_handlers"):
            return SlackRouter(mock_app)

    @pytest.fixture
    def mock_session(self):
        """Create a mock planning session."""
        session = Mock(spec=PlanningSession)
        session.id = 123
        session.user_id = "U123456"
        session.status = PlanStatus.IN_PROGRESS
        session.scheduled_for = datetime.utcnow()
        return session

    @pytest.mark.asyncio
    async def test_session_lookup_not_found(self, router):
        """Test session lookup when no session is found."""
        result = await router._get_session_by_thread("123.456", "C123456")
        assert result is None

    @pytest.mark.asyncio
    async def test_postpone_action_execution(self, router, mock_session):
        """Test execution of postpone action."""
        mock_say = AsyncMock()
        intent = PlannerAction(action="postpone", minutes=30)

        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="123.456",
            user_id="U123456",
            say=mock_say,
        )

        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "postponed by 30 minutes" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_mark_done_action_execution(self, router, mock_session):
        """Test execution of mark done action."""
        mock_say = AsyncMock()
        intent = PlannerAction(action="mark_done")

        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="123.456",
            user_id="U123456",
            say=mock_say,
        )

        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "marked as complete" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_recreate_event_action_execution(self, router, mock_session):
        """Test execution of recreate event action."""
        mock_say = AsyncMock()
        intent = PlannerAction(action="recreate_event")

        # Mock the recreate_event method
        mock_session.recreate_event = AsyncMock(return_value=True)

        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="123.456",
            user_id="U123456",
            say=mock_say,
        )

        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "Calendar event recreated successfully" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_unknown_action_handling(self, router, mock_session):
        """Test handling of unknown action types."""
        mock_say = AsyncMock()
        # Create an intent with an action that won't match any handlers
        intent = Mock()
        intent.action = "unknown_action"

        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="123.456",
            user_id="U123456",
            say=mock_say,
        )

        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "don't know how to handle" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_action_execution_error_handling(self, router, mock_session):
        """Test error handling during action execution."""
        mock_say = AsyncMock()
        intent = PlannerAction(action="postpone", minutes=15)

        # Force an error in the postpone handler
        with patch.object(
            router, "_handle_postpone_action", side_effect=Exception("Test error")
        ):
            await router._execute_structured_action(
                intent=intent,
                planning_session=mock_session,
                thread_ts="123.456",
                user_id="U123456",
                say=mock_say,
            )

        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "error processing your request" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_planning_thread_reply_processing(self, router, mock_session):
        """Test processing of planning thread replies."""
        mock_say = AsyncMock()

        with patch(
            "productivity_bot.slack_router.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.return_value = PlannerAction(action="mark_done")

            await router._process_planning_thread_reply(
                thread_ts="123.456",
                user_text="done",
                user_id="U123456",
                channel="C123456",
                planning_session=mock_session,
                say=mock_say,
            )

            mock_intent.assert_called_once_with("done")
            mock_say.assert_called_once()

    @pytest.mark.asyncio
    async def test_planning_thread_reply_error_handling(self, router, mock_session):
        """Test error handling in planning thread reply processing."""
        mock_say = AsyncMock()

        with patch(
            "productivity_bot.slack_router.send_to_planner_intent",
            side_effect=Exception("Parse error"),
        ):
            await router._process_planning_thread_reply(
                thread_ts="123.456",
                user_text="unclear text",
                user_id="U123456",
                channel="C123456",
                planning_session=mock_session,
                say=mock_say,
            )

            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "couldn't understand your request" in call_args.kwargs["text"]


class TestCalendarEventRecreation:
    """Test calendar event recreation functionality."""

    @pytest.mark.asyncio
    async def test_recreate_event_success(self):
        """Test successful calendar event recreation."""
        session = Mock(spec=PlanningSession)
        session.id = 123
        session.user_id = "U123456"
        session.date = datetime.now().date()
        session.scheduled_for = datetime.utcnow()
        session.goals = "Test goals"

        with patch("productivity_bot.models.mcp_query") as mock_mcp:
            mock_mcp.return_value = {"success": True, "event_id": "event_123"}

            # Import the method from the actual class
            from productivity_bot.models import PlanningSession

            result = await PlanningSession.recreate_event(session)

            assert result is True
            assert session.event_id == "event_123"

    @pytest.mark.asyncio
    async def test_recreate_event_failure(self):
        """Test calendar event recreation failure."""
        session = Mock(spec=PlanningSession)
        session.id = 123
        session.user_id = "U123456"
        session.date = datetime.now().date()
        session.scheduled_for = datetime.utcnow()
        session.goals = "Test goals"

        with patch("productivity_bot.models.mcp_query") as mock_mcp:
            mock_mcp.return_value = {"success": False}

            from productivity_bot.models import PlanningSession

            result = await PlanningSession.recreate_event(session)

            assert result is False

    @pytest.mark.asyncio
    async def test_recreate_event_exception_handling(self):
        """Test exception handling in calendar event recreation."""
        session = Mock(spec=PlanningSession)
        session.id = 123
        session.user_id = "U123456"
        session.date = datetime.now().date()
        session.scheduled_for = datetime.utcnow()
        session.goals = "Test goals"

        with patch(
            "productivity_bot.models.mcp_query", side_effect=Exception("MCP error")
        ):
            from productivity_bot.models import PlanningSession

            result = await PlanningSession.recreate_event(session)

            assert result is False


class TestEndToEndWorkflow:
    """Test end-to-end workflow scenarios."""

    @pytest.mark.asyncio
    async def test_complete_postpone_workflow(self):
        """Test complete postpone workflow from intent to execution."""
        # Mock all the components
        mock_app = Mock()
        mock_session = Mock(spec=PlanningSession)
        mock_session.id = 123
        mock_session.scheduled_for = datetime.utcnow()
        mock_say = AsyncMock()

        # Create router
        with patch.object(SlackRouter, "_register_handlers"):
            router = SlackRouter(mock_app)

        # Mock the intent parsing
        with patch(
            "productivity_bot.slack_router.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.return_value = PlannerAction(action="postpone", minutes=20)

            # Process the thread reply
            await router._process_planning_thread_reply(
                thread_ts="123.456",
                user_text="postpone 20",
                user_id="U123456",
                channel="C123456",
                planning_session=mock_session,
                say=mock_say,
            )

            # Verify the workflow
            mock_intent.assert_called_once_with("postpone 20")
            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "postponed by 20 minutes" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_complete_mark_done_workflow(self):
        """Test complete mark done workflow from intent to execution."""
        mock_app = Mock()
        mock_session = Mock(spec=PlanningSession)
        mock_session.id = 123
        mock_say = AsyncMock()

        with patch.object(SlackRouter, "_register_handlers"):
            router = SlackRouter(mock_app)

        with patch(
            "productivity_bot.slack_router.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.return_value = PlannerAction(action="mark_done")

            await router._process_planning_thread_reply(
                thread_ts="123.456",
                user_text="finished",
                user_id="U123456",
                channel="C123456",
                planning_session=mock_session,
                say=mock_say,
            )

            mock_intent.assert_called_once_with("finished")
            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "marked as complete" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_complete_recreate_event_workflow(self):
        """Test complete recreate event workflow from intent to execution."""
        mock_app = Mock()
        mock_session = Mock(spec=PlanningSession)
        mock_session.id = 123
        mock_session.recreate_event = AsyncMock(return_value=True)
        mock_say = AsyncMock()

        with patch.object(SlackRouter, "_register_handlers"):
            router = SlackRouter(mock_app)

        with patch(
            "productivity_bot.slack_router.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.return_value = PlannerAction(action="recreate_event")

            await router._process_planning_thread_reply(
                thread_ts="123.456",
                user_text="recreate the event",
                user_id="U123456",
                channel="C123456",
                planning_session=mock_session,
                say=mock_say,
            )

            mock_intent.assert_called_once_with("recreate the event")
            mock_session.recreate_event.assert_called_once()
            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "Calendar event recreated successfully" in call_args.kwargs["text"]


if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v"])
