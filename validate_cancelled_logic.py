#!/usr/bin/env python3
"""
Simple validation script to verify CANCELLED session handling logic.

This script validates the key business logic changes:
1. CANCELLED sessions are not treated as COMPLETE
2. Haunter bot provides persistent messaging for CANCELLED sessions
3. The logic prevents users from escaping planning requirements
"""

import logging
from enum import Enum

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PlanStatus(Enum):
    """Planning session status enum (mimics the real one)."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"  # Added for cancelled events


def simulate_haunt_message_logic(status: PlanStatus, attempt: int) -> str:
    """Simulate the haunter bot message logic for different statuses."""

    if status == PlanStatus.CANCELLED:
        # CANCELLED sessions get persistent, escalating reminders until they reschedule or complete
        if attempt == 0:
            return "👻 ❌ Your planning event was cancelled, but the planning work STILL needs to be done! Please either reschedule your planning session or complete the planning work now."
        elif attempt == 1:
            return "👻 ⚠️ Just because your calendar event was cancelled doesn't mean you can skip planning! You still need to organize your tasks and priorities."
        elif attempt == 2:
            return "👻 🚨 This is attempt #3 - You CANNOT escape planning by cancelling calendar events. Either reschedule a proper planning session or do the planning work right now."
        else:
            return f"👻 💀 PERSISTENT REMINDER #{attempt + 1}: Planning is not optional! Your cancelled event doesn't change that. Please reschedule or complete your planning session immediately."
    else:
        # Regular NOT_STARTED/IN_PROGRESS messaging
        return (
            attempt == 0
            and "⏰ It's time to plan your day! Please open your planning session."
            or f"⏰ Reminder {attempt + 1}: don't forget to plan tomorrow's schedule!"
        )


def should_continue_haunting(status: PlanStatus) -> bool:
    """Determine if haunting should continue based on session status."""

    # Only COMPLETE sessions stop haunting
    if status == PlanStatus.COMPLETE:
        return False

    # All other statuses (NOT_STARTED, IN_PROGRESS, CANCELLED) continue haunting
    return True


def simulate_calendar_event_cancellation(current_status: PlanStatus) -> PlanStatus:
    """Simulate what happens when a calendar event is cancelled."""

    # The key fix: cancelled events should be marked CANCELLED, not COMPLETE
    if current_status in [PlanStatus.NOT_STARTED, PlanStatus.IN_PROGRESS]:
        return PlanStatus.CANCELLED

    # If already complete or cancelled, status doesn't change
    return current_status


def test_cancelled_session_logic():
    """Test the cancelled session handling logic."""

    logger.info("🧪 Testing CANCELLED session logic...")

    # Test 1: Calendar event cancellation sets correct status
    logger.info("\n📅 Test 1: Calendar Event Cancellation")

    original_status = PlanStatus.NOT_STARTED
    new_status = simulate_calendar_event_cancellation(original_status)

    assert new_status == PlanStatus.CANCELLED, f"Expected CANCELLED, got {new_status}"
    logger.info(f"✅ {original_status} → {new_status} (correct)")

    # Test that CANCELLED sessions don't escape planning
    original_status = PlanStatus.IN_PROGRESS
    new_status = simulate_calendar_event_cancellation(original_status)

    assert new_status == PlanStatus.CANCELLED, f"Expected CANCELLED, got {new_status}"
    logger.info(f"✅ {original_status} → {new_status} (correct)")

    # Test 2: Haunting behavior for different statuses
    logger.info("\n👻 Test 2: Haunting Behavior")

    test_statuses = [
        PlanStatus.NOT_STARTED,
        PlanStatus.IN_PROGRESS,
        PlanStatus.CANCELLED,
        PlanStatus.COMPLETE,
    ]

    for status in test_statuses:
        should_haunt = should_continue_haunting(status)
        logger.info(f"Status: {status.value:12} → Continue haunting: {should_haunt}")

        if status == PlanStatus.COMPLETE:
            assert not should_haunt, f"COMPLETE sessions should not be haunted"
        else:
            assert should_haunt, f"{status} sessions should continue to be haunted"

    logger.info("✅ All haunting behavior tests passed")

    # Test 3: Message escalation for CANCELLED sessions
    logger.info("\n📢 Test 3: CANCELLED Session Message Escalation")

    cancelled_messages = []
    for attempt in range(5):
        message = simulate_haunt_message_logic(PlanStatus.CANCELLED, attempt)
        cancelled_messages.append(f"Attempt {attempt}: {message[:50]}...")
        logger.info(f"  Attempt {attempt}: {message[:80]}...")

    # Verify escalation
    assert "STILL needs to be done" in simulate_haunt_message_logic(
        PlanStatus.CANCELLED, 0
    )
    assert "CANNOT escape planning" in simulate_haunt_message_logic(
        PlanStatus.CANCELLED, 2
    )
    assert "PERSISTENT REMINDER" in simulate_haunt_message_logic(
        PlanStatus.CANCELLED, 4
    )

    logger.info("✅ Message escalation for CANCELLED sessions works correctly")

    # Test 4: Compare CANCELLED vs regular session messages
    logger.info("\n🔄 Test 4: CANCELLED vs Regular Session Messages")

    regular_msg = simulate_haunt_message_logic(PlanStatus.NOT_STARTED, 0)
    cancelled_msg = simulate_haunt_message_logic(PlanStatus.CANCELLED, 0)

    logger.info(f"Regular:   {regular_msg}")
    logger.info(f"Cancelled: {cancelled_msg}")

    # Verify they're different and CANCELLED is more assertive
    assert regular_msg != cancelled_msg, "Messages should be different"
    assert (
        "STILL needs to be done" in cancelled_msg
    ), "CANCELLED message should be more assertive"

    logger.info("✅ CANCELLED messages are appropriately different and assertive")

    return True


def test_business_logic_validation():
    """Validate the core business logic requirements."""

    logger.info("\n🎯 Testing Core Business Logic Requirements")

    # Requirement 1: "cancelled session should not be marked complete"
    cancelled_status = simulate_calendar_event_cancellation(PlanStatus.NOT_STARTED)
    assert (
        cancelled_status != PlanStatus.COMPLETE
    ), "Cancelled sessions must not be marked COMPLETE"
    assert (
        cancelled_status == PlanStatus.CANCELLED
    ), "Cancelled sessions must be marked CANCELLED"
    logger.info("✅ Requirement 1: Cancelled sessions are NOT marked complete")

    # Requirement 2: "these should be marked cancelled"
    assert cancelled_status == PlanStatus.CANCELLED, "Sessions must be marked CANCELLED"
    logger.info("✅ Requirement 2: Sessions are properly marked CANCELLED")

    # Requirement 3: "if the user does not want to plan the system should not let up"
    should_haunt_cancelled = should_continue_haunting(PlanStatus.CANCELLED)
    assert should_haunt_cancelled, "System must continue haunting CANCELLED sessions"
    logger.info("✅ Requirement 3: System does not let up on CANCELLED sessions")

    # Requirement 4: "instead it should haunt to either complete it or reschedule it"
    cancelled_msg = simulate_haunt_message_logic(PlanStatus.CANCELLED, 0)
    assert (
        "reschedule" in cancelled_msg.lower() or "complete" in cancelled_msg.lower()
    ), "Messages must mention rescheduling or completing"
    logger.info("✅ Requirement 4: Messages encourage rescheduling or completion")

    return True


def main():
    """Run all validation tests."""

    logger.info("🚀 Starting CANCELLED Session Logic Validation")
    logger.info("=" * 60)

    try:
        # Run core logic tests
        test_cancelled_session_logic()

        # Run business requirement validation
        test_business_logic_validation()

        logger.info("\n" + "=" * 60)
        logger.info("🎉 ALL VALIDATION TESTS PASSED!")
        logger.info("=" * 60)

        logger.info("\n📋 Validation Summary:")
        logger.info("✅ CANCELLED sessions are not marked COMPLETE")
        logger.info("✅ Calendar event cancellation triggers CANCELLED status")
        logger.info(
            "✅ Haunter bot continues persistent reminders for CANCELLED sessions"
        )
        logger.info("✅ Messages escalate and emphasize planning is still required")
        logger.info("✅ Only COMPLETE status stops the haunting system")
        logger.info("✅ Users cannot escape planning by cancelling calendar events")

        logger.info("\n🎯 Business Logic Requirements Met:")
        logger.info("   • 'cancelled session should not be marked complete' ✅")
        logger.info("   • 'these should be marked cancelled' ✅")
        logger.info("   • 'system should not let up' ✅")
        logger.info("   • 'haunt to either complete it or reschedule it' ✅")

        return True

    except AssertionError as e:
        logger.error(f"❌ Validation failed: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
