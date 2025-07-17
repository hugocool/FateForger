#!/usr/bin/env python3
"""
Simple validation script for Ticket 5: Check file structure and code presence
"""

import logging
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_file_exists(file_path: str, description: str) -> bool:
    """Check if a file exists and log result."""
    path = Path(file_path)
    if path.exists():
        logger.info(f"✅ {description}: {file_path}")
        return True
    else:
        logger.error(f"❌ {description}: {file_path} - NOT FOUND")
        return False


def check_code_contains(file_path: str, search_text: str, description: str) -> bool:
    """Check if a file contains specific text."""
    path = Path(file_path)
    if not path.exists():
        logger.error(f"❌ {description}: {file_path} - FILE NOT FOUND")
        return False

    try:
        content = path.read_text()
        if search_text in content:
            logger.info(f"✅ {description}: Found '{search_text}' in {file_path}")
            return True
        else:
            logger.error(f"❌ {description}: '{search_text}' NOT FOUND in {file_path}")
            return False
    except Exception as e:
        logger.error(f"❌ {description}: Error reading {file_path}: {e}")
        return False


def main():
    """Main validation function."""
    logger.info("=" * 60)
    logger.info("TICKET 5 STRUCTURE VALIDATION")
    logger.info("=" * 60)

    results = []

    # 1. Check slack_utils.py has __all__ export
    results.append(
        check_code_contains(
            "src/productivity_bot/slack_utils.py",
            "__all__",
            "slack_utils.__all__ export",
        )
    )

    # 2. Check models.py has slack_sched_ids column
    results.append(
        check_code_contains(
            "src/productivity_bot/models.py",
            "slack_sched_ids",
            "PlanningSession.slack_sched_ids column",
        )
    )

    # 3. Check BaseHaunter has _stop_reminders method
    results.append(
        check_code_contains(
            "src/productivity_bot/haunting/base_haunter.py",
            "def _stop_reminders",
            "BaseHaunter._stop_reminders method",
        )
    )

    # 4. Check bootstrap haunter has daily scheduling methods
    bootstrap_methods = [
        "def schedule_daily",
        "def _run_daily_check",
        "def _daily_check",
        "def _start_bootstrap_haunt",
    ]
    for method in bootstrap_methods:
        results.append(
            check_code_contains(
                "src/productivity_bot/haunting/bootstrap/haunter.py",
                method,
                f"BootstrapHaunter {method} method",
            )
        )

    # 5. Check commitment haunter has event-start methods
    commitment_methods = ["def start_event_haunt", "def _check_started_timeout"]
    for method in commitment_methods:
        results.append(
            check_code_contains(
                "src/productivity_bot/haunting/commitment/haunter.py",
                method,
                f"CommitmentHaunter {method} method",
            )
        )

    # 6. Check incomplete haunter has overdue session methods
    incomplete_methods = ["def poll_overdue_sessions", "def start_incomplete_haunt"]
    for method in incomplete_methods:
        results.append(
            check_code_contains(
                "src/productivity_bot/haunting/incomplete/haunter.py",
                method,
                f"IncompleteHaunter {method} method",
            )
        )

    # 7. Check scheduler has schedule_event_haunt
    results.append(
        check_code_contains(
            "src/productivity_bot/scheduler.py",
            "def schedule_event_haunt",
            "schedule_event_haunt function",
        )
    )

    # 8. Check for LLM integration (parse_intent methods)
    llm_files = [
        ("src/productivity_bot/haunting/bootstrap/haunter.py", "Bootstrap"),
        ("src/productivity_bot/haunting/commitment/haunter.py", "Commitment"),
        ("src/productivity_bot/haunting/incomplete/haunter.py", "Incomplete"),
    ]

    for file_path, haunter_name in llm_files:
        results.append(
            check_code_contains(
                file_path,
                "def parse_intent",
                f"{haunter_name} haunter LLM parse_intent method",
            )
        )
        results.append(
            check_code_contains(
                file_path, "AsyncOpenAI", f"{haunter_name} haunter OpenAI integration"
            )
        )

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("VALIDATION SUMMARY")
    logger.info("=" * 60)

    passed = sum(results)
    total = len(results)
    failed = total - passed

    logger.info(f"Total checks: {total}")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")

    if failed == 0:
        logger.info("\n🎉 ALL STRUCTURAL CHECKS PASSED!")
        logger.info("Ticket 5 implementation structure is complete.")
        return True
    else:
        logger.error(f"\n💥 {failed} check(s) failed.")
        logger.error("Please review the implementation.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
