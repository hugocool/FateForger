"""
Fast tests for the actions module with proper mocking.

These tests validate the actions module structure and functionality
without making any slow API calls or network requests.
"""

from unittest.mock import MagicMock, patch

import pytest

from productivity_bot.actions.planner_action import (
    PlannerAction,
    get_planner_system_message,
)
from productivity_bot.actions.prompt_utils import PromptRenderer


class TestPlannerAction:
    """Test PlannerAction model with fast validation."""

    def test_postpone_action(self):
        """Test postpone action creation and properties."""
        action = PlannerAction(action="postpone", minutes=30)
        assert action.is_postpone
        assert not action.is_mark_done
        assert not action.is_recreate_event
        assert action.get_postpone_minutes() == 30

    def test_postpone_default_minutes(self):
        """Test postpone action with default minutes."""
        action = PlannerAction(action="postpone", minutes=None)
        assert action.get_postpone_minutes() == 15  # Default

    def test_mark_done_action(self):
        """Test mark done action."""
        action = PlannerAction(action="mark_done", minutes=None)
        assert action.is_mark_done
        assert not action.is_postpone
        assert action.get_postpone_minutes() is None

    def test_recreate_event_action(self):
        """Test recreate event action."""
        action = PlannerAction(action="recreate_event", minutes=None)
        assert action.is_recreate_event
        assert not action.is_postpone

    def test_unknown_action(self):
        """Test unknown action."""
        action = PlannerAction(action="unknown", minutes=None)
        assert action.is_unknown
        assert not action.is_postpone

    def test_string_representation(self):
        """Test string representation."""
        action = PlannerAction(action="postpone", minutes=25)
        assert "postpone(25min)" in str(action)

        action = PlannerAction(action="mark_done", minutes=None)
        assert str(action) == "mark_done"


class TestPromptRenderer:
    """Test PromptRenderer with fast validation."""

    def test_render_with_schema(self):
        """Test schema injection in template."""
        renderer = PromptRenderer()
        template = "Schema: {{ schema | tojson }}"

        result = renderer.render_with_schema(template, PlannerAction)

        # Should contain schema properties
        assert "properties" in result
        assert "action" in result
        assert "minutes" in result

    def test_get_schema_only(self):
        """Test schema extraction."""
        renderer = PromptRenderer()
        schema = renderer.get_schema_only(PlannerAction)

        assert isinstance(schema, dict)
        assert "properties" in schema
        assert "action" in schema["properties"]

    def test_custom_variables(self):
        """Test template with custom variables."""
        renderer = PromptRenderer()
        template = "Model: {{ model_name }}, Custom: {{ custom_var }}"

        result = renderer.render_with_schema(
            template, PlannerAction, custom_var="test_value"
        )

        assert "Model: PlannerAction" in result
        assert "Custom: test_value" in result


class TestSystemMessage:
    """Test system message generation."""

    def test_system_message_generation(self):
        """Test that system message is properly generated."""
        message = get_planner_system_message()

        # Should be substantial
        assert len(message) > 200

        # Should contain key elements
        assert "PlannerAction" in message or "schema" in message
        assert "postpone" in message
        assert "mark_done" in message
        assert "recreate_event" in message
        assert "properties" in message  # JSON schema

    def test_system_message_contains_examples(self):
        """Test that system message contains examples."""
        message = get_planner_system_message()

        # Should contain example phrases
        assert "gimme" in message.lower()
        assert "done" in message.lower()
        assert "calendar" in message.lower()


@pytest.mark.asyncio
async def test_mock_agent_integration():
    """Test that our agent can be mocked for fast testing."""

    # Mock the entire agent process
    async def mock_process_reply(user_text, context=None):
        """Fast mock that returns based on input."""
        if "postpone" in user_text.lower():
            return PlannerAction(action="postpone", minutes=15)
        elif "done" in user_text.lower():
            return PlannerAction(action="mark_done", minutes=None)
        else:
            return PlannerAction(action="unknown", minutes=None)

    # Test the mock
    result1 = await mock_process_reply("postpone 10")
    assert result1.is_postpone

    result2 = await mock_process_reply("done")
    assert result2.is_mark_done

    result3 = await mock_process_reply("random text")
    assert result3.is_unknown

    # This should complete in milliseconds, not seconds


def test_validation_constraints():
    """Test Pydantic validation constraints."""

    # Valid actions should work
    for action in ["postpone", "mark_done", "recreate_event", "unknown"]:
        result = PlannerAction(action=action, minutes=None)
        assert result.action == action

    # Minutes constraints
    action = PlannerAction(action="postpone", minutes=60)
    assert action.minutes == 60

    # Test with valid minutes range
    action = PlannerAction(action="postpone", minutes=1440)  # 24 hours
    assert action.minutes == 1440


if __name__ == "__main__":
    # Run tests directly if executed
    pytest.main([__file__, "-v"])
