#!/usr/bin/env python3
"""
Minimal test of the structured intent parsing functionality.

This test directly imports and tests the PlannerAction model without
going through the complex package structure.
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


def main():
    """Run the minimal tests."""
    print("🚀 Running Minimal Structured Intent Tests\n")

    # Test 1: PlannerAction model
    test1_passed = test_planner_action_model()

    # Test 2: OpenAI structured output
    test2_passed = asyncio.run(test_openai_structured_output())

    # Summary
    passed = sum([test1_passed, test2_passed])
    total = 2

    print(f"\n📊 Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! The structured intent parsing system is working.")
        return 0
    else:
        print("💥 Some tests failed!")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
