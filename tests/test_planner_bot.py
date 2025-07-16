#!/usr/bin/env python3
"""
Test script for the async Slack planner bot.
Verifies that all components are working correctly.
"""

import os
import sys
import asyncio
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))


async def test_planner_bot():
    """Test the planner bot functionality."""
    print("🧪 Testing Async Slack Planner Bot...")

    # Set minimal environment variables for testing
    test_env = {
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "SLACK_SIGNING_SECRET": "test-secret",
        "SLACK_APP_TOKEN": "xapp-test-token",
        "OPENAI_API_KEY": "sk-test-key",
        "CALENDAR_WEBHOOK_SECRET": "webhook-secret",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
    }

    for key, value in test_env.items():
        os.environ[key] = value

    try:
        # Test imports
        print("✅ Testing imports...")
        from productivity_bot.planner_bot import PlannerBot
        from productivity_bot.models import PlanningSession, PlanStatus
        from productivity_bot.database import PlanningSessionService

        print("✅ All imports successful!")

        # Test bot instantiation
        print("✅ Testing bot instantiation...")
        bot = PlannerBot()
        print(f"✅ Bot created with AsyncApp: {type(bot.app).__name__}")

        # Test database models
        print("✅ Testing database models...")
        from datetime import date, datetime

        # Test enum
        assert PlanStatus.NOT_STARTED.value == "NOT_STARTED"
        assert PlanStatus.IN_PROGRESS.value == "IN_PROGRESS"
        assert PlanStatus.COMPLETE.value == "COMPLETE"
        print("✅ PlanStatus enum working correctly!")

        # Test configuration
        print("✅ Testing configuration...")
        config = bot.config
        assert config.slack_bot_token == "xoxb-test-token"
        assert config.slack_signing_secret == "test-secret"
        print("✅ Configuration loaded correctly!")

        # Test async app setup
        print("✅ Testing async app setup...")
        assert hasattr(bot.app, "client")
        assert hasattr(bot, "start")
        assert hasattr(bot, "stop")
        print("✅ Async app methods available!")

        print("\n🎉 All tests passed! The async Slack planner bot is ready to use.")
        print("\n📋 Next steps:")
        print("1. Copy .env.template to .env")
        print("2. Fill in your actual Slack tokens")
        print("3. Run: poetry run python -m src.productivity_bot.planner_bot")

        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_dependencies():
    """Test that all required dependencies are installed."""
    print("🔍 Checking dependencies...")

    required_modules = [
        "slack_bolt",
        "slack_bolt.async_app",
        "slack_bolt.adapter.socket_mode.aiohttp",
        "aiohttp",
        "sqlalchemy",
        "pydantic",
        "pydantic_settings",
    ]

    for module in required_modules:
        try:
            __import__(module)
            print(f"✅ {module}")
        except ImportError as e:
            print(f"❌ {module}: {e}")
            return False

    print("✅ All dependencies installed!")
    return True


if __name__ == "__main__":
    print("🚀 Async Slack Planner Bot Test Suite")
    print("=" * 50)

    # Test dependencies first
    if not test_dependencies():
        print("\n❌ Dependency test failed. Run 'poetry install' to fix.")
        sys.exit(1)

    print()

    # Test the bot
    success = asyncio.run(test_planner_bot())

    if success:
        sys.exit(0)
    else:
        sys.exit(1)
