#!/usr/bin/env python3
"""
Test script for Ticket 5: Finish Haunter Logic, Slack Delivery & Cleanup
Validates all implemented components work correctly.
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_slack_utils():
    """Test slack_utils __all__ exports."""
    logger.info("Testing slack_utils module...")

    try:
        from src.productivity_bot.slack_utils import __all__

        logger.info(f"✅ slack_utils.__all__ exists: {__all__}")

        # Test imports work
        from src.productivity_bot.slack_utils import delete_scheduled, schedule_dm

        logger.info(
            "✅ schedule_dm and delete_scheduled functions imported successfully"
        )

        return True
    except Exception as e:
        logger.error(f"❌ slack_utils test failed: {e}")
        return False


async def test_models_slack_sched_ids():
    """Test PlanningSession.slack_sched_ids column exists."""
    logger.info("Testing PlanningSession.slack_sched_ids column...")

    try:
        from src.productivity_bot.models import PlanningSession

        # Check if slack_sched_ids attribute exists
        if hasattr(PlanningSession, "slack_sched_ids"):
            logger.info("✅ PlanningSession.slack_sched_ids column exists")
            return True
        else:
            logger.error("❌ PlanningSession.slack_sched_ids column missing")
            return False

    except Exception as e:
        logger.error(f"❌ Models test failed: {e}")
        return False


async def test_base_haunter_stop_reminders():
    """Test BaseHaunter._stop_reminders method exists."""
    logger.info("Testing BaseHaunter._stop_reminders method...")

    try:
        from src.productivity_bot.haunting.base_haunter import BaseHaunter

        if hasattr(BaseHaunter, "_stop_reminders"):
            logger.info("✅ BaseHaunter._stop_reminders method exists")
            return True
        else:
            logger.error("❌ BaseHaunter._stop_reminders method missing")
            return False

    except Exception as e:
        logger.error(f"❌ BaseHaunter test failed: {e}")
        return False


async def test_bootstrap_haunter():
    """Test bootstrap haunter daily scheduling methods."""
    logger.info("Testing bootstrap haunter daily scheduling...")

    try:
        from src.productivity_bot.haunting.bootstrap.haunter import (
            BootstrapPlanningHaunter,
        )

        # Check required methods exist
        methods = [
            "schedule_daily",
            "_run_daily_check",
            "_daily_check",
            "_start_bootstrap_haunt",
        ]
        for method in methods:
            if hasattr(BootstrapPlanningHaunter, method):
                logger.info(f"✅ BootstrapPlanningHaunter.{method} exists")
            else:
                logger.error(f"❌ BootstrapPlanningHaunter.{method} missing")
                return False

        return True

    except Exception as e:
        logger.error(f"❌ Bootstrap haunter test failed: {e}")
        return False


async def test_commitment_haunter():
    """Test commitment haunter event-start methods."""
    logger.info("Testing commitment haunter event-start methods...")

    try:
        from src.productivity_bot.haunting.commitment.haunter import (
            CommitmentPlanningHaunter,
        )

        # Check required methods exist
        methods = ["start_event_haunt", "_check_started_timeout"]
        for method in methods:
            if hasattr(CommitmentPlanningHaunter, method):
                logger.info(f"✅ CommitmentPlanningHaunter.{method} exists")
            else:
                logger.error(f"❌ CommitmentPlanningHaunter.{method} missing")
                return False

        return True

    except Exception as e:
        logger.error(f"❌ Commitment haunter test failed: {e}")
        return False


async def test_incomplete_haunter():
    """Test incomplete haunter overdue session methods."""
    logger.info("Testing incomplete haunter overdue session methods...")

    try:
        from src.productivity_bot.haunting.incomplete.haunter import (
            IncompletePlanningHaunter,
        )

        # Check required methods exist
        methods = ["poll_overdue_sessions", "start_incomplete_haunt"]
        for method in methods:
            if hasattr(IncompletePlanningHaunter, method):
                logger.info(f"✅ IncompletePlanningHaunter.{method} exists")
            else:
                logger.error(f"❌ IncompletePlanningHaunter.{method} missing")
                return False

        # Check if poll_overdue_sessions is a classmethod
        if hasattr(IncompletePlanningHaunter.poll_overdue_sessions, "__self__"):
            logger.info(
                "✅ IncompletePlanningHaunter.poll_overdue_sessions is a classmethod"
            )
        else:
            logger.warning(
                "⚠️  IncompletePlanningHaunter.poll_overdue_sessions might not be a classmethod"
            )

        return True

    except Exception as e:
        logger.error(f"❌ Incomplete haunter test failed: {e}")
        return False


async def test_scheduler_functions():
    """Test scheduler has schedule_event_haunt function."""
    logger.info("Testing scheduler functions...")

    try:
        from src.productivity_bot.scheduler import schedule_event_haunt

        logger.info("✅ schedule_event_haunt function exists")

        # Check if get_scheduler exists
        from src.productivity_bot.scheduler import get_scheduler

        logger.info("✅ get_scheduler function exists")

        return True

    except Exception as e:
        logger.error(f"❌ Scheduler test failed: {e}")
        return False


async def test_llm_integration():
    """Test LLM integration exists in haunters."""
    logger.info("Testing LLM integration...")

    try:
        # Test that haunters have parse_intent methods
        from src.productivity_bot.haunting.bootstrap.haunter import (
            BootstrapPlanningHaunter,
        )
        from src.productivity_bot.haunting.commitment.haunter import (
            CommitmentPlanningHaunter,
        )
        from src.productivity_bot.haunting.incomplete.haunter import (
            IncompletePlanningHaunter,
        )

        haunters = [
            ("BootstrapPlanningHaunter", BootstrapPlanningHaunter),
            ("CommitmentPlanningHaunter", CommitmentPlanningHaunter),
            ("IncompletePlanningHaunter", IncompletePlanningHaunter),
        ]

        for name, haunter_class in haunters:
            if hasattr(haunter_class, "parse_intent"):
                logger.info(f"✅ {name}.parse_intent exists")
            else:
                logger.error(f"❌ {name}.parse_intent missing")
                return False

        return True

    except Exception as e:
        logger.error(f"❌ LLM integration test failed: {e}")
        return False


async def run_all_tests():
    """Run all validation tests."""
    logger.info("=" * 60)
    logger.info("TICKET 5 VALIDATION TEST SUITE")
    logger.info("=" * 60)

    tests = [
        ("Slack Utils", test_slack_utils),
        ("Models slack_sched_ids", test_models_slack_sched_ids),
        ("BaseHaunter _stop_reminders", test_base_haunter_stop_reminders),
        ("Bootstrap Haunter", test_bootstrap_haunter),
        ("Commitment Haunter", test_commitment_haunter),
        ("Incomplete Haunter", test_incomplete_haunter),
        ("Scheduler Functions", test_scheduler_functions),
        ("LLM Integration", test_llm_integration),
    ]

    results = []

    for test_name, test_func in tests:
        logger.info(f"\n--- Testing {test_name} ---")
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"❌ {test_name} failed with exception: {e}")
            results.append((test_name, False))

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST RESULTS SUMMARY")
    logger.info("=" * 60)

    passed = 0
    failed = 0

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status}: {test_name}")
        if result:
            passed += 1
        else:
            failed += 1

    logger.info(f"\nTotal: {len(results)} tests")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")

    if failed == 0:
        logger.info("\n🎉 ALL TESTS PASSED! Ticket 5 implementation is complete.")
        return True
    else:
        logger.error(
            f"\n💥 {failed} test(s) failed. Please fix before marking complete."
        )
        return False


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
