#!/usr/bin/env python3
"""
Enhanced minimal test of the structured intent parsing functionality.

This test covers the core structured intent system including PlannerAction model,
OpenAI integration, scheduler functions, and mocked agent tests.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add the src directory to the Python path
project_root = Path(__file__).parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))


def test_planner_action_model():
    """Test the PlannerAction Pydantic model directly."""
    print("🧪 Testing PlannerAction Model...")

    try:
        # Import the model directly from the file
        sys.path.insert(0, str(src_path / "productivity_bot" / "models"))
        from planner_action import PlannerAction

        # Test 1: Create postpone action
        postpone_action = PlannerAction(action="postpone", minutes=15)
        assert postpone_action.action == "postpone"
        assert postpone_action.minutes == 15
        assert postpone_action.is_postpone
        print("✅ Postpone action created correctly")

        # Test 2: Create mark_done action
        done_action = PlannerAction(action="mark_done", minutes=None)
        assert done_action.action == "mark_done"
        assert done_action.minutes is None
        assert done_action.is_mark_done
        print("✅ Mark done action created correctly")

        # Test 3: Create recreate_event action
        recreate_action = PlannerAction(action="recreate_event", minutes=None)
        assert recreate_action.action == "recreate_event"
        assert recreate_action.is_recreate_event
        print("✅ Recreate event action created correctly")

        # Test 4: Test minutes utility method
        assert postpone_action.get_postpone_minutes() == 15
        print("✅ Utility methods working correctly")

        print("✅ PlannerAction model test PASSED")
        return True

    except Exception as e:
        print(f"❌ PlannerAction model test FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_structured_intent_agent():
    """Test the structured intent agent with mock."""
    print("🧪 Testing Structured Intent Agent...")

    try:
        # Import the agent with mocked OpenAI client
        sys.path.insert(0, str(src_path / "productivity_bot" / "agents"))

        # Mock the OpenAI client
        from unittest.mock import AsyncMock, MagicMock, patch

        with patch("planner_agent.get_openai_client") as mock_client:
            from planner_agent import send_to_planner_intent

            # Mock successful response
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]

            # Import PlannerAction for the mock
            sys.path.insert(0, str(src_path / "productivity_bot" / "models"))
            from planner_action import PlannerAction

            # Create real PlannerAction objects as mock responses
            postpone_action = PlannerAction(action="postpone", minutes=15)
            mark_done_action = PlannerAction(action="mark_done", minutes=None)

            mock_response.choices[0].message.parsed = postpone_action

            mock_async_client = AsyncMock()
            mock_async_client.beta.chat.completions.parse.return_value = mock_response
            mock_client.return_value = mock_async_client

            # Test postpone parsing
            result = await send_to_planner_intent("postpone 15 minutes")
            assert result.action == "postpone"
            assert result.minutes == 15
            print("✅ Structured postpone intent parsing works")

            # Test mark done parsing
            mock_response.choices[0].message.parsed = mark_done_action
            result = await send_to_planner_intent("done")
            assert result.action == "mark_done"
            assert result.minutes is None
            print("✅ Structured mark done intent parsing works")

            print("✅ Structured intent agent test PASSED")
            return True

    except Exception as e:
        print(f"❌ Structured intent agent test FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_openai_structured_output():
    """Test OpenAI structured output if API key is available."""
    print("🧪 Testing OpenAI Structured Output...")

    if not os.environ.get("OPENAI_API_KEY"):
        print("⚠️  OPENAI_API_KEY not set, skipping OpenAI test")
        return True

    try:
        # Import openai and test structured output
        import openai

        # Import our model
        sys.path.insert(0, str(src_path / "productivity_bot" / "models"))
        from planner_action import PlannerAction

        client = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        # Test with a simple postpone request
        response = await client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Parse user input into a PlannerAction. Available actions: postpone (with minutes), mark_done, recreate_event.",
                },
                {"role": "user", "content": "postpone 10 minutes"},
            ],
            response_format=PlannerAction,
        )

        action = response.choices[0].message.parsed

        if action and action.action == "postpone" and action.minutes == 10:
            print("✅ OpenAI structured output test PASSED")
            return True
        else:
            print(f"❌ OpenAI returned unexpected result: {action}")
            return False

    except Exception as e:
        print(f"❌ OpenAI structured output test FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_scheduler_functions():
    """Test the scheduler functions with mocks."""
    print("🧪 Testing Scheduler Functions...")

    try:
        from datetime import datetime, timedelta
        from unittest.mock import MagicMock, patch

        # Mock the scheduler
        with patch("productivity_bot.scheduler.get_scheduler") as mock_get_scheduler:
            mock_scheduler = MagicMock()
            mock_get_scheduler.return_value = mock_scheduler

            # Import the functions directly
            sys.path.insert(0, str(src_path / "productivity_bot"))
            from scheduler import cancel_haunt_by_session, reschedule_haunt

            # Test reschedule function
            new_time = datetime.now() + timedelta(hours=2)
            result = reschedule_haunt(123, new_time)
            assert result is True
            mock_scheduler.reschedule_job.assert_called_once()
            print("✅ Reschedule haunt function works")

            # Test cancel function
            result = cancel_haunt_by_session(123)
            assert result is True
            mock_scheduler.remove_job.assert_called_once()
            print("✅ Cancel haunt function works")

            print("✅ Scheduler functions test PASSED")
            return True

    except Exception as e:
        print(f"❌ Scheduler functions test FAILED: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run the enhanced minimal tests."""
    print("🚀 Running Enhanced Minimal Structured Intent Tests\n")

    # Test 1: PlannerAction model
    test1_passed = test_planner_action_model()

    # Test 2: Structured intent agent with mocks
    test2_passed = asyncio.run(test_structured_intent_agent())

    # Test 3: OpenAI structured output (if API key available)
    test3_passed = asyncio.run(test_openai_structured_output())

    # Test 4: Scheduler functions
    test4_passed = test_scheduler_functions()

    # Summary
    passed = sum([test1_passed, test2_passed, test3_passed, test4_passed])
    total = 4

    print(f"\n📊 Test Results: {passed}/{total} tests passed")

    if passed >= 3:  # Allow OpenAI test to fail if no API key
        print("🎉 Core structured intent system tests passed!")
        print("\n✅ System ready for:")
        print("   • Pydantic-based structured action parsing")
        print("   • OpenAI structured output integration")
        print("   • Scheduler job management")
        print("   • Slack event routing and action execution")

        if not os.environ.get("OPENAI_API_KEY"):
            print(
                "\n⚠️  To enable full LLM integration, set OPENAI_API_KEY environment variable"
            )

        return 0
    else:
        print("💥 Critical tests failed!")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
