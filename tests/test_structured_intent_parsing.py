"""
Unit tests for structured LLM intent parsing.

This module tests the new PlannerAction-based structured output system
that replaces regex parsing with constrained LLM generation.
"""

import asyncio
from unittest.mock import Mock, patch

import pytest

from productivity_bot.agents.planner_agent import send_to_planner_intent
from productivity_bot.models.planner_action import PlannerAction


class TestPlannerAction:
    """Test the PlannerAction Pydantic model."""

    def test_postpone_action_creation(self):
        """Test creating postpone actions with minutes."""
        action = PlannerAction(action="postpone", minutes=15)
        assert action.action == "postpone"
        assert action.minutes == 15
        assert action.is_postpone
        assert not action.is_mark_done
        assert not action.is_recreate_event

    def test_mark_done_action_creation(self):
        """Test creating mark done actions."""
        action = PlannerAction(action="mark_done", minutes=None)
        assert action.action == "mark_done"
        assert action.minutes is None
        assert not action.is_postpone
        assert action.is_mark_done
        assert not action.is_recreate_event

    def test_recreate_event_action_creation(self):
        """Test creating recreate event actions."""
        action = PlannerAction(action="recreate_event", minutes=None)
        assert action.action == "recreate_event"
        assert action.minutes is None
        assert not action.is_postpone
        assert not action.is_mark_done
        assert action.is_recreate_event

    def test_get_postpone_minutes_with_value(self):
        """Test getting postpone minutes when specified."""
        action = PlannerAction(action="postpone", minutes=30)
        assert action.get_postpone_minutes() == 30
        assert action.get_postpone_minutes(default=60) == 30

    def test_get_postpone_minutes_with_default(self):
        """Test getting postpone minutes with default fallback."""
        action = PlannerAction(action="postpone", minutes=None)
        assert action.get_postpone_minutes() == 15  # default
        assert action.get_postpone_minutes(default=45) == 45

    def test_get_postpone_minutes_non_postpone_action(self):
        """Test getting postpone minutes for non-postpone actions."""
        action = PlannerAction(action="mark_done", minutes=None)
        assert action.get_postpone_minutes() == 0

    def test_string_representation(self):
        """Test string representation for logging."""
        postpone_action = PlannerAction(action="postpone", minutes=15)
        assert "postpone" in str(postpone_action)
        assert "15" in str(postpone_action)

        done_action = PlannerAction(action="mark_done", minutes=None)
        assert "mark_done" in str(done_action)


class TestStructuredIntentParsing:
    """Test the structured intent parsing functionality."""

    @pytest.mark.asyncio
    async def test_postpone_15_minutes(self):
        """Unit test: send_to_planner_intent("postpone 15") → PlannerAction(action="postpone", minutes=15)."""
        result = await send_to_planner_intent("postpone 15")

        assert isinstance(result, PlannerAction)
        assert result.action == "postpone"
        assert result.minutes == 15
        assert result.is_postpone

    @pytest.mark.asyncio
    async def test_postpone_30_minutes_natural_language(self):
        """Test parsing natural language postpone requests."""
        result = await send_to_planner_intent("delay for 30 minutes")

        assert isinstance(result, PlannerAction)
        assert result.action == "postpone"
        assert result.minutes == 30

    @pytest.mark.asyncio
    async def test_postpone_without_minutes(self):
        """Test postpone request without specific minutes."""
        result = await send_to_planner_intent("postpone")

        assert isinstance(result, PlannerAction)
        assert result.action == "postpone"
        # Should get default minutes
        assert result.get_postpone_minutes() == 15

    @pytest.mark.asyncio
    async def test_mark_done_simple(self):
        """Test simple done command."""
        result = await send_to_planner_intent("done")

        assert isinstance(result, PlannerAction)
        assert result.action == "mark_done"
        assert result.minutes is None
        assert result.is_mark_done

    @pytest.mark.asyncio
    async def test_mark_done_natural_language(self):
        """Test natural language completion commands."""
        test_cases = [
            "finished",
            "complete",
            "I'm done with this",
            "finished with planning",
        ]

        for text in test_cases:
            result = await send_to_planner_intent(text)
            assert isinstance(result, PlannerAction)
            assert result.action == "mark_done"
            assert result.is_mark_done

    @pytest.mark.asyncio
    async def test_recreate_event(self):
        """Test recreate event commands."""
        result = await send_to_planner_intent("recreate event")

        assert isinstance(result, PlannerAction)
        assert result.action == "recreate_event"
        assert result.minutes is None
        assert result.is_recreate_event

    @pytest.mark.asyncio
    async def test_recreate_event_variations(self):
        """Test recreate event command variations."""
        test_cases = [
            "create the event again",
            "reschedule",
            "recreate the calendar event",
        ]

        for text in test_cases:
            result = await send_to_planner_intent(text)
            assert isinstance(result, PlannerAction)
            assert result.action == "recreate_event"

    @pytest.mark.asyncio
    async def test_invalid_input_fallback(self):
        """Test fallback behavior for unclear input."""
        # Edge case test: if intent.minutes is None on postpone, default to 0 or ask for clarification
        result = await send_to_planner_intent("please wait")

        # Should return a valid PlannerAction (fallback to mark_done)
        assert isinstance(result, PlannerAction)
        assert result.action in ["postpone", "mark_done", "recreate_event"]

    @pytest.mark.asyncio
    async def test_empty_input_handling(self):
        """Test handling of empty or meaningless input."""
        test_cases = ["", "   ", "xyz123", "random text"]

        for text in test_cases:
            result = await send_to_planner_intent(text)
            assert isinstance(result, PlannerAction)
            # Should always return a valid action, not crash
            assert result.action in ["postpone", "mark_done", "recreate_event"]


class TestSlackIntegrationScenarios:
    """Test Slack integration scenarios with mocked components."""

    @pytest.mark.asyncio
    async def test_slack_postpone_10_minutes_scenario(self):
        """
        Slack integration test: in a real DM thread, user types "postpone 10",
        bot schedules job + replies "OK, I'll check back in 10 minutes."
        """
        # Mock the Slack say function
        mock_say = Mock()

        # Import here to avoid circular imports in test setup
        # Mock planning session - use Mock instead of importing
        from unittest.mock import Mock

        from productivity_bot.slack_event_router import SlackEventRouter

        # Create a mock planning session
        mock_session = Mock(spec=PlanningSession)
        mock_session.id = 123

        # Create router instance with mock Slack app
        mock_app = Mock()
        router = SlackEventRouter(mock_app)

        # Test the structured action execution
        intent = PlannerAction(action="postpone", minutes=10)

        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="test_thread",
            user_id="test_user",
            say=mock_say,
        )

        # Verify the response
        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "10 minutes" in call_args.kwargs["text"]
        assert call_args.kwargs["thread_ts"] == "test_thread"

    @pytest.mark.asyncio
    async def test_slack_mark_done_scenario(self):
        """Test mark done scenario with Slack integration."""
        # Mock the Slack say function
        mock_say = Mock()

        from productivity_bot.slack_event_router import SlackEventRouter

        # Create a mock planning session
        mock_session = Mock()
        mock_session.id = 456

        # Create router instance
        mock_app = Mock()
        router = SlackEventRouter(mock_app)

        # Test mark done action
        intent = PlannerAction(action="mark_done", minutes=None)

        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="test_thread",
            user_id="test_user",
            say=mock_say,
        )

        # Verify the response
        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "Good work" in call_args.kwargs["text"]
        assert "✅" in call_args.kwargs["text"]

    @pytest.mark.asyncio
    async def test_slack_invalid_input_error_message(self):
        """
        Invalid input test: user types unclear text, bot replies with
        the fallback "❌ Sorry, I couldn't understand…" message.
        """
        # This would be tested at the router level where exceptions are caught
        mock_say = Mock()

        from productivity_bot.models import PlanningSession
        from productivity_bot.slack_event_router import SlackEventRouter

        mock_session = Mock(spec=PlanningSession)
        mock_app = Mock()
        router = SlackEventRouter(mock_app)

        # Simulate an exception during intent parsing (would be handled in _process_planning_thread_reply)
        with patch(
            "productivity_bot.agents.planner_agent.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.side_effect = Exception("Parsing failed")

            # This should trigger the error handling
            await router._process_planning_thread_reply(
                thread_ts="test_thread",
                user_text="incomprehensible input",
                user_id="test_user",
                channel="test_channel",
                planning_session=mock_session,
                say=mock_say,
            )

            # Verify error message
            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "❌ Sorry, I couldn't understand" in call_args.kwargs["text"]
            assert "postpone X minutes" in call_args.kwargs["text"]


def run_tests():
    """Run all tests and display results."""
    import sys

    print("🧪 Running Structured Intent Parsing Tests")
    print("=" * 50)

    # Run the tests
    exit_code = pytest.main([__file__, "-v", "--tb=short", "--asyncio-mode=auto"])

    if exit_code == 0:
        print("\n✅ All tests passed! Structured intent parsing is working correctly.")
        print("\n📋 Test Coverage:")
        print("  ✅ PlannerAction model validation")
        print("  ✅ Structured intent parsing accuracy")
        print("  ✅ Slack integration scenarios")
        print("  ✅ Error handling and fallbacks")

        print("\n🎯 Acceptance Criteria Met:")
        print(
            "  ✅ send_to_planner_intent('postpone 15') → PlannerAction(action='postpone', minutes=15)"
        )
        print("  ✅ Slack thread: 'postpone 10' → 'OK, I'll check back in 10 minutes'")
        print("  ✅ Invalid input → 'Sorry, I couldn't understand...' message")
        print("  ✅ Edge case: postpone without minutes handled with defaults")
    else:
        print("\n❌ Some tests failed. Check output above for details.")

    return exit_code


if __name__ == "__main__":
    exit(run_tests())
