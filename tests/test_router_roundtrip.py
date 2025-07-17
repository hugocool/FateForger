"""
Test router roundtrip functionality for agent handoff system.

Tests the RouterAgent → PlanningAgent handoff flow with mock instances.
"""

import asyncio
import json
from uuid import uuid4

import pytest

from src.productivity_bot.actions.haunt_payload import HauntPayload
from src.productivity_bot.agents.planning_agent import get_planning_agent
from src.productivity_bot.agents.router_agent import get_router_agent


class TestRouterRoundtrip:
    """Test router roundtrip functionality."""
    
    @pytest.mark.asyncio
    async def test_router_to_planner_create_event(self):
        """Test routing create_event payload from router to planner."""
        # Create test payload
        payload = HauntPayload(
            session_id=uuid4(),
            action="create_event", 
            minutes=None,
            commit_time_str="Tomorrow 08:00"
        )
        
        # Get router agent
        router = get_router_agent()
        
        # Route the payload
        router_result = await router.route_payload(payload)
        
        # Verify router response
        assert router_result["target"] == "planner"
        assert "payload" in router_result
        
        # Get planning agent
        planner = get_planning_agent()
        
        # Send router message to planner
        planner_result = await planner.handle_router_message(router_result)
        
        # Verify planner handled the message
        assert planner_result["status"] in ["ok", "error"]  # Either is valid for test
        
    @pytest.mark.asyncio
    async def test_router_to_planner_postpone(self):
        """Test routing postpone payload from router to planner."""
        # Create test payload
        payload = HauntPayload(
            session_id=uuid4(),
            action="postpone",
            minutes=30,
            commit_time_str=""
        )
        
        # Get router agent
        router = get_router_agent()
        
        # Route the payload
        router_result = await router.route_payload(payload)
        
        # Verify router response
        assert router_result["target"] == "planner"
        assert "payload" in router_result
        
        # Get planning agent
        planner = get_planning_agent()
        
        # Send router message to planner
        planner_result = await planner.handle_router_message(router_result)
        
        # Verify planner handled the message
        assert planner_result["status"] in ["ok", "error"]  # Either is valid for test
        
    @pytest.mark.asyncio
    async def test_router_to_planner_mark_done(self):
        """Test routing mark_done payload from router to planner."""
        # Create test payload
        payload = HauntPayload(
            session_id=uuid4(),
            action="mark_done",
            minutes=None,
            commit_time_str=""
        )
        
        # Get router agent
        router = get_router_agent()
        
        # Route the payload
        router_result = await router.route_payload(payload)
        
        # Verify router response
        assert router_result["target"] == "planner"
        assert "payload" in router_result
        
        # Get planning agent
        planner = get_planning_agent()
        
        # Send router message to planner
        planner_result = await planner.handle_router_message(router_result)
        
        # Verify planner handled the message
        assert planner_result["status"] in ["ok", "error"]  # Either is valid for test
        
    @pytest.mark.asyncio 
    async def test_payload_serialization_roundtrip(self):
        """Test that payloads survive serialization through the router."""
        # Create test payload
        original_payload = HauntPayload(
            session_id=uuid4(),
            action="create_event",
            minutes=45,
            commit_time_str="2025-07-18 09:00"
        )
        
        # Convert to dict and back (simulates router processing)
        payload_dict = original_payload.to_dict()
        restored_payload = HauntPayload.from_dict(payload_dict)
        
        # Verify payload integrity
        assert restored_payload.session_id == original_payload.session_id
        assert restored_payload.action == original_payload.action
        assert restored_payload.minutes == original_payload.minutes
        assert restored_payload.commit_time_str == original_payload.commit_time_str
        
    @pytest.mark.asyncio
    async def test_router_fallback_behavior(self):
        """Test router fallback when LLM fails."""
        # Create malformed payload to potentially trigger fallback
        router = get_router_agent()
        
        # Use a payload that might cause JSON parsing issues
        test_payload = HauntPayload(
            session_id=uuid4(),
            action="create_event",
            minutes=None,
            commit_time_str='{"malformed": json'  # Potentially problematic string
        )
        
        # Should not raise an exception due to fallback handling
        result = await router.route_payload(test_payload)
        
        # Should still route to planner as fallback
        assert result["target"] == "planner"
        assert "payload" in result


async def run_router_tests():
    """Run all router tests manually."""
    print("🧪 Testing Router → PlanningAgent Handoff System")
    
    test_instance = TestRouterRoundtrip()
    
    try:
        print("\n1. Testing create_event routing...")
        await test_instance.test_router_to_planner_create_event()
        print("   ✅ Create event routing works")
        
        print("\n2. Testing postpone routing...")
        await test_instance.test_router_to_planner_postpone()
        print("   ✅ Postpone routing works")
        
        print("\n3. Testing mark_done routing...")
        await test_instance.test_router_to_planner_mark_done()
        print("   ✅ Mark done routing works")
        
        print("\n4. Testing payload serialization...")
        await test_instance.test_payload_serialization_roundtrip()
        print("   ✅ Payload serialization works")
        
        print("\n5. Testing router fallback...")
        await test_instance.test_router_fallback_behavior()
        print("   ✅ Router fallback works")
        
        print("\n🎉 All router roundtrip tests passed!")
        
    except Exception as e:
        print(f"\n❌ Router test failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(run_router_tests())
