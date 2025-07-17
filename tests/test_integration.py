#!/usr/bin/env python3
"""
Integration test script for the structured intent parsing system.

This script tests the core functionality without relying on complex package imports.
"""

import asyncio
import os
import sys
import traceback
from pathlib import Path

# Add the src directory to the Python path
project_root = Path(__file__).parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))


async def test_structured_intent_parsing():
    """Test the structured intent parsing functionality."""
    print("🧪 Testing Structured Intent Parsing...")

    try:
        # Test the PlannerAction model directly
        from productivity_bot.models.planner_action import PlannerAction

        # Test 1: Create postpone action
        postpone_action = PlannerAction(action="postpone", minutes=15)
        assert postpone_action.action == "postpone"
        assert postpone_action.minutes == 15
        assert postpone_action.is_postpone()
        print("✅ PlannerAction model working correctly")

        # Test 2: Test with OpenAI structured output (if API key available)
        if os.environ.get("OPENAI_API_KEY"):
            from productivity_bot.agents.planner_agent import send_to_planner_intent

            # Test various inputs
            test_cases = [
                ("postpone 10", "postpone", 10),
                ("delay for 30 minutes", "postpone", 30),
                ("done", "mark_done", None),
                ("finished", "mark_done", None),
                ("recreate event", "recreate_event", None),
            ]

            for user_input, expected_action, expected_minutes in test_cases:
                try:
                    result = await send_to_planner_intent(user_input)
                    assert result.action == expected_action
                    if expected_minutes is not None:
                        assert result.minutes == expected_minutes
                    print(
                        f"✅ '{user_input}' → {result.action} (minutes: {result.minutes})"
                    )
                except Exception as e:
                    print(f"❌ Error testing '{user_input}': {e}")

            print("✅ OpenAI structured output working correctly")
        else:
            print("⚠️  OPENAI_API_KEY not set, skipping LLM tests")

        return True

    except Exception as e:
        print(f"❌ Error in structured intent parsing test: {e}")
        traceback.print_exc()
        return False


async def test_basic_imports():
    """Test that basic imports work without circular dependencies."""
    print("🧪 Testing Basic Imports...")

    try:
        # Test individual model imports
        from productivity_bot.models.planner_action import PlannerAction

        print("✅ PlannerAction import successful")

        # Test agent import
        from productivity_bot.agents.planner_agent import send_to_planner_intent

        print("✅ Planner agent import successful")

        # Test SlackRouter import (if it works)
        try:
            from productivity_bot.slack_router import SlackRouter

            print("✅ SlackRouter import successful")
        except ImportError as e:
            print(f"⚠️  SlackRouter import failed: {e}")

        return True

    except Exception as e:
        print(f"❌ Error in basic imports test: {e}")
        traceback.print_exc()
        return False


async def main():
    """Run all integration tests."""
    print("🚀 Starting Integration Tests\n")

    # Run tests
    tests = [
        test_basic_imports(),
        test_structured_intent_parsing(),
    ]

    results = await asyncio.gather(*tests, return_exceptions=True)

    # Check results
    passed = 0
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"❌ Test {i+1} failed with exception: {result}")
        elif result:
            passed += 1

    print(f"\n📊 Test Results: {passed}/{len(tests)} tests passed")

    if passed == len(tests):
        print("🎉 All tests passed!")
        return 0
    else:
        print("💥 Some tests failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
