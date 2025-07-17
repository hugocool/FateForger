#!/usr/bin/env python3
"""
Integration test for the structured output system.

This script tests the complete implementation:
1. Structured LLM intent parsing with PlannerAction
2. Slack router integration
3. Database session management
4. Calendar event recreation
5. End-to-end workflow validation
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


async def test_structured_intent_parsing():
    """Test structured LLM intent parsing."""
    print("🧠 Testing structured LLM intent parsing...")

    try:
        from productivity_bot.agents.planner_agent import (
            send_to_planner_intent,
            test_planner_agent,
        )
        from productivity_bot.models.planner_action import PlannerAction

        # Test 1: Create PlannerAction objects directly
        postpone_action = PlannerAction(action="postpone", minutes=30)
        assert postpone_action.is_postpone()
        assert postpone_action.get_postpone_minutes() == 30
        print("  ✅ PlannerAction model validation works")

        done_action = PlannerAction(action="mark_done")
        assert done_action.is_mark_done()
        print("  ✅ Mark done action validation works")

        recreate_action = PlannerAction(action="recreate_event")
        assert recreate_action.is_recreate_event()
        print("  ✅ Recreate event action validation works")

        # Test 2: Mock LLM parsing (since we don't have OpenAI key for testing)
        with patch(
            "productivity_bot.agents.planner_agent.get_openai_client"
        ) as mock_client:
            mock_response = Mock()
            mock_response.choices = [Mock()]
            mock_response.choices[0].message.parsed = PlannerAction(
                action="postpone", minutes=15
            )

            mock_client.return_value.beta.chat.completions.parse = AsyncMock(
                return_value=mock_response
            )

            result = await send_to_planner_intent("postpone 15")
            assert result.action == "postpone"
            assert result.minutes == 15
            print("  ✅ Mocked LLM intent parsing works")

        # Test 3: Error handling
        with patch(
            "productivity_bot.agents.planner_agent.get_openai_client"
        ) as mock_client:
            mock_client.return_value.beta.chat.completions.parse = AsyncMock(
                side_effect=Exception("Test error")
            )

            result = await send_to_planner_intent("unclear text")
            assert result.action == "mark_done"  # Safe fallback
            print("  ✅ Error handling with safe fallback works")

        print("✅ Structured intent parsing tests passed!")
        return True

    except Exception as e:
        print(f"❌ Structured intent parsing test failed: {e}")
        return False


async def test_slack_router():
    """Test the new Slack router."""
    print("\n🤖 Testing Slack router...")

    try:
        from productivity_bot.models.planner_action import PlannerAction
        from productivity_bot.slack_router import SlackRouter

        # Create mock Slack app
        mock_app = Mock()

        # Create router (with handler registration mocked)
        with patch.object(SlackRouter, "_register_handlers"):
            router = SlackRouter(mock_app)
            print("  ✅ SlackRouter instantiation works")

        # Test session lookup (should return None for now)
        session = await router._get_session_by_thread("test_thread", "test_channel")
        assert session is None
        print("  ✅ Session lookup returns None as expected")

        # Test action execution with mocked session
        mock_session = Mock()
        mock_session.id = 123
        mock_session.scheduled_for = datetime.utcnow()
        mock_say = AsyncMock()

        # Test postpone action
        intent = PlannerAction(action="postpone", minutes=20)
        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="test_thread",
            user_id="test_user",
            say=mock_say,
        )

        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "postponed by 20 minutes" in call_args.kwargs["text"]
        print("  ✅ Postpone action execution works")

        # Test mark done action
        mock_say.reset_mock()
        intent = PlannerAction(action="mark_done")
        await router._execute_structured_action(
            intent=intent,
            planning_session=mock_session,
            thread_ts="test_thread",
            user_id="test_user",
            say=mock_say,
        )

        mock_say.assert_called_once()
        call_args = mock_say.call_args
        assert "marked as complete" in call_args.kwargs["text"]
        print("  ✅ Mark done action execution works")

        print("✅ Slack router tests passed!")
        return True

    except Exception as e:
        print(f"❌ Slack router test failed: {e}")
        return False


async def test_calendar_event_recreation():
    """Test calendar event recreation."""
    print("\n📅 Testing calendar event recreation...")

    try:
        from productivity_bot.models import PlanningSession

        # Create mock session
        mock_session = Mock()
        mock_session.id = 123
        mock_session.user_id = "test_user"
        mock_session.date = datetime.now().date()
        mock_session.scheduled_for = datetime.utcnow()
        mock_session.goals = "Test goals"
        mock_session.event_id = None

        # Test successful recreation
        with patch("productivity_bot.models.mcp_query") as mock_mcp:
            mock_mcp.return_value = {"success": True, "event_id": "new_event_123"}

            result = await PlanningSession.recreate_event(mock_session)
            assert result is True
            assert mock_session.event_id == "new_event_123"
            print("  ✅ Successful calendar event recreation works")

        # Test failed recreation
        with patch("productivity_bot.models.mcp_query") as mock_mcp:
            mock_mcp.return_value = {"success": False}

            result = await PlanningSession.recreate_event(mock_session)
            assert result is False
            print("  ✅ Failed calendar event recreation handled correctly")

        # Test exception handling
        with patch(
            "productivity_bot.models.mcp_query", side_effect=Exception("MCP error")
        ):
            result = await PlanningSession.recreate_event(mock_session)
            assert result is False
            print("  ✅ Calendar event recreation exception handling works")

        print("✅ Calendar event recreation tests passed!")
        return True

    except Exception as e:
        print(f"❌ Calendar event recreation test failed: {e}")
        return False


async def test_planner_bot_integration():
    """Test PlannerBot integration with new router."""
    print("\n🏗️ Testing PlannerBot integration...")

    try:
        from productivity_bot.planner_bot import PlannerBot
        from productivity_bot.slack_router import SlackRouter

        # Mock config and dependencies
        with (
            patch("productivity_bot.planner_bot.get_config") as mock_config,
            patch("productivity_bot.planner_bot.AsyncApp") as mock_app_class,
            patch("productivity_bot.planner_bot.AutoGenPlannerAgent") as mock_autogen,
        ):

            mock_config.return_value.slack_bot_token = "test_token"
            mock_config.return_value.slack_app_token = "test_app_token"

            mock_app = Mock()
            mock_app_class.return_value = mock_app

            # Create PlannerBot
            bot = PlannerBot()

            # Verify it has the new SlackRouter
            assert hasattr(bot, "slack_router")
            assert isinstance(bot.slack_router, SlackRouter)
            print("  ✅ PlannerBot uses new SlackRouter")

        print("✅ PlannerBot integration tests passed!")
        return True

    except Exception as e:
        print(f"❌ PlannerBot integration test failed: {e}")
        return False


async def test_end_to_end_workflow():
    """Test complete end-to-end workflow."""
    print("\n🔄 Testing end-to-end workflow...")

    try:
        from productivity_bot.models.planner_action import PlannerAction
        from productivity_bot.slack_router import SlackRouter

        # Mock components
        mock_app = Mock()
        mock_session = Mock()
        mock_session.id = 123
        mock_session.scheduled_for = datetime.utcnow()
        mock_say = AsyncMock()

        # Create router
        with patch.object(SlackRouter, "_register_handlers"):
            router = SlackRouter(mock_app)

        # Test complete postpone workflow
        with patch(
            "productivity_bot.slack_router.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.return_value = PlannerAction(action="postpone", minutes=25)

            await router._process_planning_thread_reply(
                thread_ts="test_thread",
                user_text="postpone 25",
                user_id="test_user",
                channel="test_channel",
                planning_session=mock_session,
                say=mock_say,
            )

            mock_intent.assert_called_once_with("postpone 25")
            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "postponed by 25 minutes" in call_args.kwargs["text"]
            print("  ✅ Complete postpone workflow works")

        # Test complete mark done workflow
        mock_say.reset_mock()
        with patch(
            "productivity_bot.slack_router.send_to_planner_intent"
        ) as mock_intent:
            mock_intent.return_value = PlannerAction(action="mark_done")

            await router._process_planning_thread_reply(
                thread_ts="test_thread",
                user_text="done",
                user_id="test_user",
                channel="test_channel",
                planning_session=mock_session,
                say=mock_say,
            )

            mock_intent.assert_called_once_with("done")
            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "marked as complete" in call_args.kwargs["text"]
            print("  ✅ Complete mark done workflow works")

        # Test error handling workflow
        mock_say.reset_mock()
        with patch(
            "productivity_bot.slack_router.send_to_planner_intent",
            side_effect=Exception("Parse error"),
        ):
            await router._process_planning_thread_reply(
                thread_ts="test_thread",
                user_text="unclear text",
                user_id="test_user",
                channel="test_channel",
                planning_session=mock_session,
                say=mock_say,
            )

            mock_say.assert_called_once()
            call_args = mock_say.call_args
            assert "couldn't understand your request" in call_args.kwargs["text"]
            print("  ✅ Error handling workflow works")

        print("✅ End-to-end workflow tests passed!")
        return True

    except Exception as e:
        print(f"❌ End-to-end workflow test failed: {e}")
        return False


async def main():
    """Run all integration tests."""
    print("🧪 Starting comprehensive structured output system integration tests...\n")

    tests = [
        test_structured_intent_parsing,
        test_slack_router,
        test_calendar_event_recreation,
        test_planner_bot_integration,
        test_end_to_end_workflow,
    ]

    results = []
    for test in tests:
        try:
            result = await test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test {test.__name__} failed with exception: {e}")
            results.append(False)

    print(f"\n📊 Test Results:")
    print(f"   Passed: {sum(results)}/{len(results)}")
    print(f"   Failed: {len(results) - sum(results)}/{len(results)}")

    if all(results):
        print(
            "\n🎉 All tests passed! The structured output system is working correctly."
        )
        return True
    else:
        print("\n❌ Some tests failed. Please check the implementation.")
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
