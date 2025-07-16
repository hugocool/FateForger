"""
Test script for the new Slack Event Router and Agent integration.

This script tests the core functionality of the new agent system:
1. MCP client connection to calendar tools
2. Planner agent for parsing user commands
3. Slack event router for thread management
4. Integration with existing PlannerBot

Run this script to validate the implementation:
    python -m productivity_bot.test_agent_integration
"""

import asyncio
import logging
from typing import Any, Dict

from .agents.mcp_client import get_calendar_tools, test_mcp_connection
from .agents.planner_agent import send_to_planner, test_planner_agent
from .common import get_logger
from .slack_event_router import test_event_router

logger = get_logger("test_agent_integration")


async def test_mcp_integration() -> bool:
    """Test MCP client integration with calendar tools."""
    logger.info("Testing MCP integration...")

    try:
        # Test connection
        connection_ok = await test_mcp_connection()
        if not connection_ok:
            logger.warning("MCP connection test failed - continuing with other tests")
            return False

        # Test tool discovery
        tools = await get_calendar_tools()
        logger.info(f"Found {len(tools)} calendar tools")

        return True

    except Exception as e:
        logger.error(f"MCP integration test failed: {e}")
        return False


async def test_planner_integration() -> bool:
    """Test planner agent integration."""
    logger.info("Testing planner agent integration...")

    try:
        # Test planner agent
        agent_ok = await test_planner_agent()
        if not agent_ok:
            logger.error("Planner agent test failed")
            return False

        # Test command parsing
        test_commands = ["postpone 15", "done", "help", "status", "recreate event"]

        for command in test_commands:
            response = await send_to_planner("test_thread", command)
            logger.info(f"Command '{command}' → {response}")

            # Validate response structure
            if not isinstance(response, dict) or "action" not in response:
                logger.error(
                    f"Invalid response format for command '{command}': {response}"
                )
                return False

        return True

    except Exception as e:
        logger.error(f"Planner integration test failed: {e}")
        return False


async def test_slack_router_integration() -> bool:
    """Test Slack event router integration."""
    logger.info("Testing Slack event router integration...")

    try:
        # Test event router
        router_ok = await test_event_router()
        if not router_ok:
            logger.error("Event router test failed")
            return False

        logger.info("Event router test passed")
        return True

    except Exception as e:
        logger.error(f"Slack router integration test failed: {e}")
        return False


async def test_full_integration() -> bool:
    """Test the complete agent integration flow."""
    logger.info("Testing full agent integration...")

    try:
        # Simulate a planning thread interaction
        test_thread_id = "test_thread_123"
        test_user_input = "postpone 30"

        # This would normally come from a real Slack thread
        # For testing, we just verify the planner agent can parse it
        response = await send_to_planner(test_thread_id, test_user_input)

        # Verify expected response
        expected_action = "postpone"
        expected_minutes = 30

        if (
            response.get("action") == expected_action
            and response.get("minutes") == expected_minutes
        ):
            logger.info("Full integration test passed")
            return True
        else:
            logger.error(f"Full integration test failed: {response}")
            return False

    except Exception as e:
        logger.error(f"Full integration test failed: {e}")
        return False


async def run_all_tests() -> Dict[str, bool]:
    """Run all integration tests and return results."""
    logger.info("🚀 Starting agent integration tests...")

    results = {}

    # Test 1: MCP Integration
    results["mcp"] = await test_mcp_integration()

    # Test 2: Planner Agent Integration
    results["planner"] = await test_planner_integration()

    # Test 3: Slack Event Router Integration
    results["slack_router"] = await test_slack_router_integration()

    # Test 4: Full Integration
    results["full_integration"] = await test_full_integration()

    # Summary
    passed = sum(1 for result in results.values() if result)
    total = len(results)

    logger.info(f"🎯 Integration test results: {passed}/{total} passed")

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"  {test_name}: {status}")

    return results


def main():
    """Main entry point for testing."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        results = asyncio.run(run_all_tests())

        # Exit with appropriate code
        all_passed = all(results.values())
        exit_code = 0 if all_passed else 1

        print(f"\n{'='*50}")
        if all_passed:
            print("🎉 All integration tests passed!")
        else:
            print("⚠️  Some integration tests failed. Check logs for details.")
        print(f"{'='*50}")

        exit(exit_code)

    except Exception as e:
        logger.error(f"Test execution failed: {e}")
        exit(1)


if __name__ == "__main__":
    main()
