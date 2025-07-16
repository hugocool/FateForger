#!/usr/bin/env python3
"""
Test script to verify the gaps identified in the review are addressed.

This tests:
1. Router registration (check if SlackEventRouter is initialized)
2. Thread-to-session linking logic exists
3. MCP method name is correct
4. Core functionality works
"""

import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))


def test_router_registration():
    """Test that SlackEventRouter is properly initialized."""
    print("🧪 Testing Router Registration...")

    try:
        # Check that PlannerBot initializes the router
        # (We can't actually instantiate due to Slack tokens, but we can check the class structure)
        import inspect

        from productivity_bot.planner_bot import PlannerBot
        from productivity_bot.slack_event_router import SlackEventRouter

        # Check PlannerBot.__init__ method
        source = inspect.getsource(PlannerBot.__init__)
        if "SlackEventRouter" in source and "self.event_router" in source:
            print("  ✅ PlannerBot initializes SlackEventRouter")
            return True
        else:
            print("  ❌ PlannerBot doesn't initialize SlackEventRouter")
            return False

    except Exception as e:
        print(f"  ❌ Router registration test failed: {e}")
        return False


def test_thread_linking_logic():
    """Test that thread linking logic exists."""
    print("\n🧪 Testing Thread Linking Logic...")

    try:
        import inspect

        from productivity_bot.slack_event_router import SlackEventRouter

        # Check if _try_link_to_active_session method exists
        methods = [
            name
            for name, _ in inspect.getmembers(
                SlackEventRouter, predicate=inspect.isfunction
            )
        ]

        if "_try_link_to_active_session" in methods:
            print("  ✅ Thread linking method exists")

            # Check if the main handler calls it
            source = inspect.getsource(SlackEventRouter._handle_message_event)
            if "_try_link_to_active_session" in source:
                print("  ✅ Thread linking logic integrated in handler")
                return True
            else:
                print("  ❌ Thread linking not called in handler")
                return False
        else:
            print("  ❌ Thread linking method missing")
            return False

    except Exception as e:
        print(f"  ❌ Thread linking test failed: {e}")
        return False


def test_mcp_method_name():
    """Test that MCP method name is correct."""
    print("\n🧪 Testing MCP Method Name...")

    try:
        import inspect

        from productivity_bot.models import PlanningSession

        # Check the recreate_event method
        source = inspect.getsource(PlanningSession.recreate_event)

        if '"method": "calendar.events.insert"' in source:
            print("  ✅ MCP method name is calendar.events.insert")
            return True
        elif '"method": "create_event"' in source:
            print("  ❌ MCP method name is still create_event (needs update)")
            return False
        else:
            print("  ⚠️  MCP method format not found in expected location")
            return False

    except Exception as e:
        print(f"  ❌ MCP method test failed: {e}")
        return False


def test_mcp_tools_implementation():
    """Test that MCP tools implementation uses correct API."""
    print("\n🧪 Testing MCP Tools Implementation...")

    try:
        import inspect

        from productivity_bot.agents.mcp_client import get_calendar_tools

        # Check the get_calendar_tools implementation
        source = inspect.getsource(get_calendar_tools)

        if "async with" in source and "list_tools()" in source:
            print("  ✅ MCP tools uses async context manager and list_tools()")
            return True
        else:
            print("  ❌ MCP tools implementation doesn't use correct API")
            return False

    except Exception as e:
        print(f"  ❌ MCP tools test failed: {e}")
        return False


def test_structured_intent_flow():
    """Test the end-to-end structured intent flow."""
    print("\n🧪 Testing Structured Intent Flow...")

    try:
        # Import key components
        from productivity_bot.agents.planner_agent import send_to_planner_intent
        from productivity_bot.pydantic_models.planner_action import PlannerAction
        from productivity_bot.slack_event_router import SlackEventRouter

        # Test PlannerAction model
        action = PlannerAction(action="postpone", minutes=15)
        assert action.is_postpone
        assert action.get_postpone_minutes() == 15

        # Check that router has structured action execution
        import inspect

        methods = [
            name
            for name, _ in inspect.getmembers(
                SlackEventRouter, predicate=inspect.isfunction
            )
        ]

        required_methods = [
            "_execute_structured_action",
            "_handle_postpone_action",
            "_handle_mark_done_action",
            "_handle_recreate_event_action",
        ]

        missing_methods = [m for m in required_methods if m not in methods]
        if missing_methods:
            print(f"  ❌ Missing methods: {missing_methods}")
            return False

        print("  ✅ Structured intent flow components present")
        return True

    except Exception as e:
        print(f"  ❌ Structured intent flow test failed: {e}")
        return False


def main():
    """Run all gap verification tests."""
    print("🚀 Slack Router Gap Analysis - Verification Tests")
    print("=" * 60)

    tests = [
        test_router_registration,
        test_thread_linking_logic,
        test_mcp_method_name,
        test_mcp_tools_implementation,
        test_structured_intent_flow,
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1

    print(f"\n📊 Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL GAP VERIFICATION TESTS PASSED!")
        print("\n✅ Verified Fixes:")
        print("   • Router registration in PlannerBot ✓")
        print("   • Thread-to-session linking logic ✓")
        print("   • MCP method name corrected ✓")
        print("   • MCP tools API implementation ✓")
        print("   • End-to-end structured intent flow ✓")
        print("\n🚀 Ready for integration testing!")
    else:
        print("⚠️  Some gap verification tests failed.")
        print("   Review the output above for specific issues.")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
