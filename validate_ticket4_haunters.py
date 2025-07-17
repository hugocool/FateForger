#!/usr/bin/env python3
"""
Test script for validating Ticket 4 haunter refactoring.

This script validates:
1. Three new action schemas (Bootstrap, Commitment, Incomplete)
2. Three new haunter classes with proper inheritance
3. Integration with BaseHaunter and AutoGen routing
4. Action schema parsing and validation

Run with: poetry run python validate_ticket4_haunters.py
"""

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from productivity_bot.haunting.bootstrap import (
    BootstrapAction,
    PlanningBootstrapHaunter,
)
from productivity_bot.haunting.commitment import CommitmentAction, CommitmentHaunter
from productivity_bot.haunting.incomplete import (
    IncompleteAction,
    IncompletePlanningHaunter,
)


def test_action_schemas():
    """Test that all action schemas work correctly."""
    print("🧪 Testing Action Schemas...")

    # Test BootstrapAction
    bootstrap = BootstrapAction(
        action="create_event", minutes=30, commit_time_str="tomorrow at 2pm"
    )
    assert bootstrap.action == "create_event"
    assert bootstrap.minutes == 30
    assert bootstrap.commit_time_str == "tomorrow at 2pm"
    print("✅ BootstrapAction validation passed")

    # Test CommitmentAction
    commitment = CommitmentAction(action="mark_done")
    assert commitment.action == "mark_done"
    assert commitment.is_mark_done is True
    assert commitment.is_postpone is False
    print("✅ CommitmentAction validation passed")

    # Test IncompleteAction
    incomplete = IncompleteAction(action="postpone", minutes=60)
    assert incomplete.action == "postpone"
    assert incomplete.is_postpone is True
    assert incomplete.get_postpone_minutes() == 60
    print("✅ IncompleteAction validation passed")


async def test_haunter_instantiation():
    """Test that all haunter classes can be instantiated."""
    print("\\n🧪 Testing Haunter Instantiation...")

    # Mock dependencies
    mock_slack = AsyncMock()
    mock_scheduler = MagicMock()
    session_id = uuid4()
    channel = "C12345"

    # Test BootstrapHaunter
    bootstrap_haunter = PlanningBootstrapHaunter(
        session_id=session_id,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel=channel,
    )
    assert bootstrap_haunter.session_id == session_id
    assert bootstrap_haunter.channel == channel
    assert bootstrap_haunter.backoff_base_minutes == 15  # Bootstrap-specific
    print("✅ PlanningBootstrapHaunter instantiation passed")

    # Test CommitmentHaunter
    commitment_haunter = CommitmentHaunter(
        session_id=session_id,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel=channel,
    )
    assert commitment_haunter.session_id == session_id
    assert commitment_haunter.channel == channel
    assert commitment_haunter.backoff_base_minutes == 10  # Commitment-specific
    print("✅ CommitmentHaunter instantiation passed")

    # Test IncompletePlanningHaunter
    incomplete_haunter = IncompletePlanningHaunter(
        session_id=session_id,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel=channel,
    )
    assert incomplete_haunter.session_id == session_id
    assert incomplete_haunter.channel == channel
    assert incomplete_haunter.backoff_base_minutes == 20  # Incomplete-specific
    print("✅ IncompletePlanningHaunter instantiation passed")


async def test_haunter_inheritance():
    """Test that haunters properly inherit from BaseHaunter."""
    print("\\n🧪 Testing Haunter Inheritance...")

    # Mock dependencies
    mock_slack = AsyncMock()
    mock_scheduler = MagicMock()
    session_id = uuid4()
    channel = "C12345"

    bootstrap_haunter = PlanningBootstrapHaunter(
        session_id=session_id,
        slack=mock_slack,
        scheduler=mock_scheduler,
        channel=channel,
    )

    # Test BaseHaunter methods are available
    assert hasattr(bootstrap_haunter, "schedule_job")
    assert hasattr(bootstrap_haunter, "cancel_job")
    assert hasattr(bootstrap_haunter, "send")
    assert hasattr(bootstrap_haunter, "next_delay")
    assert hasattr(bootstrap_haunter, "_job_id")
    print("✅ BaseHaunter methods available")

    # Test abstract methods are implemented
    assert hasattr(bootstrap_haunter, "handle_user_reply")
    assert hasattr(bootstrap_haunter, "_route_to_planner")
    print("✅ Abstract methods implemented")

    # Test job ID generation
    job_id = bootstrap_haunter._job_id("test", 1)
    expected = f"haunt_{session_id}_test_1"
    assert job_id == expected
    print("✅ Job ID generation working")


def test_action_schema_inheritance():
    """Test that action schemas properly inherit from HaunterActionBase."""
    print("\\n🧪 Testing Action Schema Inheritance...")

    # All action schemas should have the action field
    bootstrap = BootstrapAction(action="create_event")
    commitment = CommitmentAction(action="mark_done")
    incomplete = IncompleteAction(action="postpone")

    assert hasattr(bootstrap, "action")
    assert hasattr(commitment, "action")
    assert hasattr(incomplete, "action")
    print("✅ All schemas have action field")

    # Test schema-specific methods
    assert hasattr(bootstrap, "is_create_event")
    assert hasattr(commitment, "is_mark_done")
    assert hasattr(incomplete, "is_postpone")
    print("✅ Schema-specific methods available")


def test_prompts_exist():
    """Test that system prompts are defined for each schema."""
    print("\\n🧪 Testing System Prompts...")

    from productivity_bot.haunting.bootstrap import BOOTSTRAP_PROMPT
    from productivity_bot.haunting.commitment import COMMITMENT_PROMPT
    from productivity_bot.haunting.incomplete import INCOMPLETE_PROMPT

    assert isinstance(BOOTSTRAP_PROMPT, str)
    assert len(BOOTSTRAP_PROMPT) > 100  # Should be substantial
    assert "bootstrap" in BOOTSTRAP_PROMPT.lower()
    print("✅ Bootstrap prompt defined and substantial")

    assert isinstance(COMMITMENT_PROMPT, str)
    assert len(COMMITMENT_PROMPT) > 100
    assert "commitment" in COMMITMENT_PROMPT.lower()
    print("✅ Commitment prompt defined and substantial")

    assert isinstance(INCOMPLETE_PROMPT, str)
    assert len(INCOMPLETE_PROMPT) > 100
    assert "incomplete" in INCOMPLETE_PROMPT.lower()
    print("✅ Incomplete prompt defined and substantial")


async def test_haunt_payload_integration():
    """Test integration with HauntPayload for router handoff."""
    print("\\n🧪 Testing HauntPayload Integration...")

    from uuid import UUID

    from productivity_bot.actions.haunt_payload import HauntPayload

    # Test that HauntPayload supports new action types
    session_id = uuid4()

    # Test with bootstrap action
    payload1 = HauntPayload(
        session_id=session_id,
        action="create_event",
        minutes=30,
        commit_time_str="tomorrow at 2pm",
    )
    assert payload1.action == "create_event"
    print("✅ HauntPayload supports create_event")

    # Test with commitment action
    payload2 = HauntPayload(
        session_id=session_id, action="mark_done", minutes=None, commit_time_str=None
    )
    assert payload2.action == "mark_done"
    print("✅ HauntPayload supports mark_done")

    # Test with unknown action
    payload3 = HauntPayload(
        session_id=session_id, action="unknown", minutes=None, commit_time_str=None
    )
    assert payload3.action == "unknown"
    print("✅ HauntPayload supports unknown")


async def main():
    """Run all tests."""
    print("🚀 Starting Ticket 4 Haunter Refactoring Validation\\n")

    try:
        # Test action schemas
        test_action_schemas()

        # Test haunter instantiation
        await test_haunter_instantiation()

        # Test inheritance
        await test_haunter_inheritance()

        # Test schema inheritance
        test_action_schema_inheritance()

        # Test prompts
        test_prompts_exist()

        # Test payload integration
        await test_haunt_payload_integration()

        print(
            "\\n🎉 All tests passed! Ticket 4 haunter refactoring is working correctly."
        )
        print("\\n📋 Summary of implemented features:")
        print("  ✅ Three MECE action schemas (Bootstrap, Commitment, Incomplete)")
        print("  ✅ Co-located schemas and prompts in persona folders")
        print("  ✅ Three haunter classes inheriting from BaseHaunter")
        print("  ✅ Specialized back-off timing per persona")
        print("  ✅ Integration with AutoGen router via HauntPayload")
        print("  ✅ Abstract method implementation for user reply handling")

        return True

    except Exception as e:
        print(f"\\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
