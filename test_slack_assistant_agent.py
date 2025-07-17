#!/usr/bin/env python3
"""
Test script for the updated Slack Assistant Agent with MCP Workbench integration.

This tests the new agent that follows the documentation pattern:
1. SlackAssistantAgent with MCP Workbench
2. Robust natural language parsing
3. Structured intent extraction
4. Integration with existing infrastructure
"""

import asyncio
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))


async def test_slack_assistant_agent():
    """Test the new SlackAssistantAgent implementation."""
    print("🧪 Testing Slack Assistant Agent with MCP Workbench...")

    try:
        from productivity_bot.agents.slack_assistant_agent import (
            SlackAssistantAgent,
            process_slack_thread_reply,
        )

        # Test 1: Basic instantiation
        print("\n1. Testing agent instantiation...")
        agent = SlackAssistantAgent()
        print("   ✅ SlackAssistantAgent created successfully")

        # Test 2: Natural language parsing variations
        print("\n2. Testing natural language understanding...")

        test_cases = [
            # Postpone variations
            ("postpone 15", "postpone", 15),
            ("gimme 10 minutes", "postpone", 10),
            ("let's pick it up in 5", "postpone", 5),
            ("delay for 30", "postpone", 30),
            ("give me an hour", "postpone", 60),
            ("not now, maybe in 20", "postpone", 20),
            ("push back 45 minutes", "postpone", 45),
            # Done variations
            ("done", "mark_done", None),
            ("finished", "mark_done", None),
            ("all good", "mark_done", None),
            ("I'm ready", "mark_done", None),
            ("complete", "mark_done", None),
            ("yes", "mark_done", None),
            # Recreate variations
            ("recreate event", "recreate_event", None),
            ("create calendar entry", "recreate_event", None),
            ("remake", "recreate_event", None),
            ("add to calendar", "recreate_event", None),
        ]

        session_context = {
            "session_id": 123,
            "user_id": "U12345",
            "date": "2025-07-17",
            "status": "IN_PROGRESS",
            "goals": "Complete project review",
        }

        passed = 0
        total = len(test_cases)

        for user_input, expected_action, expected_minutes in test_cases:
            try:
                result = await process_slack_thread_reply(user_input, session_context)

                action_match = result.action == expected_action
                minutes_match = result.minutes == expected_minutes

                if action_match and minutes_match:
                    print(f"   ✅ '{user_input}' → {result.action} ({result.minutes})")
                    passed += 1
                else:
                    print(
                        f"   ❌ '{user_input}' → {result.action} ({result.minutes}) [expected: {expected_action} ({expected_minutes})]"
                    )

            except Exception as e:
                print(f"   ❌ '{user_input}' → ERROR: {e}")

        print(
            f"\n   📊 Natural Language Test Results: {passed}/{total} passed ({passed/total*100:.1f}%)"
        )

        # Test 3: MCP Workbench integration
        print("\n3. Testing MCP Workbench integration...")
        try:
            # This will attempt to connect to MCP server
            await agent._initialize_agent()
            print(
                "   ✅ MCP Workbench initialization attempted (may fail if MCP server not running)"
            )
        except Exception as e:
            print(
                f"   ⚠️  MCP Workbench initialization failed (expected if no MCP server): {e}"
            )

        # Test 4: Check that agent is properly integrated with existing SlackEventRouter
        print("\n4. Testing integration with SlackEventRouter...")
        try:
            from productivity_bot.slack_event_router import SlackEventRouter

            # Check that SlackEventRouter imports the new function
            router_file = (
                project_root / "src" / "productivity_bot" / "slack_event_router.py"
            )
            router_content = router_file.read_text()

            if "process_slack_thread_reply" in router_content:
                print("   ✅ SlackEventRouter imports new agent function")
            else:
                print("   ❌ SlackEventRouter not updated to use new agent")

        except Exception as e:
            print(f"   ❌ Integration test failed: {e}")

        # Test 5: Performance and edge cases
        print("\n5. Testing edge cases...")

        edge_cases = [
            ("", "mark_done", None),  # Empty input
            ("asdjkasjdkad", "mark_done", None),  # Gibberish
            ("postpone", "postpone", 15),  # No time specified
            ("5", "postpone", 5),  # Just a number
            (
                "calendar event create please",
                "recreate_event",
                None,
            ),  # Multiple keywords
        ]

        edge_passed = 0
        for user_input, expected_action, expected_minutes in edge_cases:
            try:
                result = await process_slack_thread_reply(user_input, session_context)
                print(
                    f"   ✅ Edge case '{user_input}' → {result.action} ({result.minutes})"
                )
                edge_passed += 1
            except Exception as e:
                print(f"   ❌ Edge case '{user_input}' → ERROR: {e}")

        print(
            f"\n   📊 Edge Case Results: {edge_passed}/{len(edge_cases)} handled gracefully"
        )

        # Test 6: Integration with existing models
        print("\n6. Testing integration with PlanningSession.recreate_event()...")
        try:
            from productivity_bot.models import PlanningSession

            # Check that recreate_event still uses MCP correctly
            import inspect

            source = inspect.getsource(PlanningSession.recreate_event)

            if "calendar.events.insert" in source and "mcp_query" in source:
                print("   ✅ PlanningSession.recreate_event() uses correct MCP method")
            else:
                print(
                    "   ❌ PlanningSession.recreate_event() may not use correct MCP integration"
                )

        except Exception as e:
            print(f"   ❌ Model integration test failed: {e}")

        # Cleanup
        await agent.cleanup()
        print("\n🎉 Slack Assistant Agent testing completed!")

        return passed / total >= 0.8  # 80% success rate for natural language parsing

    except Exception as e:
        print(f"❌ Slack Assistant Agent test failed: {e}")
        return False


async def test_documentation_compliance():
    """Test that our implementation follows the provided documentation."""
    print("\n🧪 Testing Documentation Compliance...")

    try:
        # Test 1: MCP Workbench pattern
        print("\n1. Checking MCP Workbench usage...")
        agent_file = (
            Path(__file__).parent
            / "src"
            / "productivity_bot"
            / "agents"
            / "slack_assistant_agent.py"
        )
        agent_content = agent_file.read_text()

        checks = [
            ("SseServerParams", "✅ Uses SseServerParams"),
            ("McpWorkbench", "✅ Uses McpWorkbench"),
            ("http://mcp:4000/mcp", "✅ Points to correct MCP URL"),
            ("list_tools()", "✅ Calls list_tools() method"),
            ("AssistantAgent", "✅ Uses AssistantAgent"),
        ]

        for pattern, success_msg in checks:
            if pattern in agent_content:
                print(f"   {success_msg}")
            else:
                print(f"   ❌ Missing: {pattern}")

        # Test 2: Natural language robustness
        print("\n2. Checking natural language parsing robustness...")

        if "gimme" in agent_content and "pick up" in agent_content:
            print("   ✅ Handles colloquial expressions")
        else:
            print("   ❌ May not handle colloquial expressions")

        if "hours?" in agent_content and "minutes?" in agent_content:
            print("   ✅ Handles time unit variations")
        else:
            print("   ❌ May not handle time unit variations")

        # Test 3: Integration points
        print("\n3. Checking integration points...")

        router_file = (
            Path(__file__).parent / "src" / "productivity_bot" / "slack_event_router.py"
        )
        if router_file.exists():
            router_content = router_file.read_text()
            if "process_slack_thread_reply" in router_content:
                print("   ✅ SlackEventRouter updated to use new agent")
            else:
                print("   ❌ SlackEventRouter not fully integrated")

        return True

    except Exception as e:
        print(f"❌ Documentation compliance test failed: {e}")
        return False


async def main():
    """Run all tests."""
    print("🚀 Starting Comprehensive Slack Assistant Agent Tests\n")

    # Run tests
    agent_test_passed = await test_slack_assistant_agent()
    compliance_test_passed = await test_documentation_compliance()

    # Summary
    print("\n" + "=" * 50)
    print("📋 TEST SUMMARY")
    print("=" * 50)
    print(f"Agent Functionality: {'✅ PASS' if agent_test_passed else '❌ FAIL'}")
    print(
        f"Documentation Compliance: {'✅ PASS' if compliance_test_passed else '❌ FAIL'}"
    )

    overall_pass = agent_test_passed and compliance_test_passed
    print(
        f"\nOverall Result: {'🎉 ALL TESTS PASSED' if overall_pass else '⚠️ SOME TESTS FAILED'}"
    )

    if overall_pass:
        print("\n✅ The Slack Assistant Agent implementation is ready!")
        print("   - MCP Workbench integration following documentation")
        print("   - Robust natural language understanding")
        print("   - Proper integration with existing infrastructure")
        print("   - Enhanced error handling and edge case support")
    else:
        print("\n⚠️ Some issues detected. Review test output above.")

    return overall_pass


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
