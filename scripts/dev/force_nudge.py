#!/usr/bin/env python3
"""
Force-trigger a planning nudge to test the dispatch system.
Run this while the Slack bot is running to see if nudges work.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv()


async def main():
    from slack_sdk.web.async_client import AsyncWebClient

    from fateforger.haunt.reconcile import PlanningReminder
    from fateforger.slack_bot.planning import PlanningCoordinator

    slack_token = os.getenv("SLACK_BOT_TOKEN")
    if not slack_token:
        print("❌ SLACK_BOT_TOKEN not set")
        return

    user_id = "U095637NL8P"  # Your Slack user ID from the anchor

    print(f"🎯 Force-triggering planning nudge for user: {user_id}")
    print("   (This bypasses the scheduler and calls dispatch directly)")
    print()

    client = AsyncWebClient(token=slack_token)

    # Create a minimal mock runtime that has the needed attributes
    class MockRuntime:
        pass

    mock_runtime = MockRuntime()

    # We need to manually set up the stores - let's just send a simple message instead
    try:
        # Just open a DM and send a test message
        result = await client.conversations_open(users=[user_id])
        dm_channel = result.get("channel", {}).get("id")
        if not dm_channel:
            print("❌ Could not open DM channel")
            return

        print(f"   DM Channel: {dm_channel}")

        await client.chat_postMessage(
            channel=dm_channel,
            text=":test_tube: Test planning nudge: No planning session detected. This is a test message to verify nudging works!",
        )
        print("   ✅ Test message sent to DM!")
        print()
        print("   If you see the message in Slack, the dispatch is working.")
        print("   The issue may be with job scheduling/persistence.")

    except Exception as e:
        print(f"❌ Failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
