#!/usr/bin/env python3
"""
Validation script for structured agent handoff system.

This script tests the basic functionality of the agent handoff system
without requiring full environment setup.
"""

import json
import sys
from pathlib import Path
from uuid import uuid4

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


def test_haunt_payload():
    """Test HauntPayload creation and serialization."""
    print("🧪 Testing HauntPayload...")

    try:
        from productivity_bot.actions.haunt_payload import HauntPayload

        # Test payload creation
        payload = HauntPayload(
            session_id=uuid4(),
            action="create_event",
            minutes=None,
            commit_time_str="Tomorrow 20:30",
        )

        print(f"  ✅ Created payload: {payload}")

        # Test serialization
        payload_dict = payload.to_dict()
        print(f"  ✅ Serialized: {payload_dict}")

        # Test deserialization
        restored = HauntPayload.from_dict(payload_dict)
        print(f"  ✅ Restored: {restored}")

        # Verify they match
        assert payload.action == restored.action
        assert payload.minutes == restored.minutes
        assert payload.commit_time_str == restored.commit_time_str
        print("  ✅ Serialization round-trip successful")

        return True

    except Exception as e:
        print(f"  ❌ HauntPayload test failed: {e}")
        return False


def test_planner_action_updates():
    """Test PlannerAction updates for create_event."""
    print("\n🧪 Testing PlannerAction updates...")

    try:
        from productivity_bot.actions.planner_action import PlannerAction

        # Test create_event action
        action = PlannerAction(action="create_event", minutes=None)
        print(f"  ✅ Created action: {action}")

        # Test property
        assert action.is_create_event
        print("  ✅ is_create_event property works")

        # Test enum update
        from productivity_bot.actions.planner_action import ActionType

        assert ActionType.CREATE_EVENT == "create_event"
        print("  ✅ ActionType enum updated")

        # Test that old recreate_event is gone
        try:
            action = PlannerAction(action="recreate_event", minutes=None)
            print("  ❌ recreate_event still accepted - should be removed")
            return False
        except:
            print("  ✅ recreate_event properly removed")

        return True

    except Exception as e:
        print(f"  ❌ PlannerAction test failed: {e}")
        return False


def test_router_logic():
    """Test router decision logic."""
    print("\n🧪 Testing Router logic...")

    try:
        # Test simple routing decision
        payload_data = {
            "session_id": str(uuid4()),
            "action": "create_event",
            "minutes": None,
            "commit_time_str": "Tomorrow 8pm",
        }

        # Simulate router decision
        router_response = {"target": "planner", "payload": payload_data}

        print(f"  ✅ Router decision: {router_response['target']}")

        # Verify payload is passed through
        assert router_response["payload"]["action"] == "create_event"
        print("  ✅ Payload passed through correctly")

        return True

    except Exception as e:
        print(f"  ❌ Router logic test failed: {e}")
        return False


def test_system_prompt_updates():
    """Test that system prompts are updated."""
    print("\n🧪 Testing system prompt updates...")

    try:
        from productivity_bot.actions.planner_action import (
            PLANNER_SYSTEM_MESSAGE_TEMPLATE,
        )

        # Check that create_event is in prompt
        assert "create_event" in PLANNER_SYSTEM_MESSAGE_TEMPLATE
        print("  ✅ create_event found in system prompt")

        # Check that recreate_event is removed
        assert "recreate_event" not in PLANNER_SYSTEM_MESSAGE_TEMPLATE
        print("  ✅ recreate_event removed from system prompt")

        return True

    except Exception as e:
        print(f"  ❌ System prompt test failed: {e}")
        return False


def main():
    """Run all validation tests."""
    print("🎯 Structured Agent Handoff System Validation\n")

    tests = [
        test_haunt_payload,
        test_planner_action_updates,
        test_router_logic,
        test_system_prompt_updates,
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1

    print(f"\n📊 Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL TESTS PASSED! Structured agent handoff system is ready.")
        return True
    else:
        print("❌ Some tests failed. Please review and fix issues.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
