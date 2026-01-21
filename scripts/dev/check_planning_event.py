#!/usr/bin/env python3
"""Check if the planning event exists on the calendar."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from dotenv import load_dotenv

load_dotenv()

from datetime import datetime, timedelta, timezone

from fateforger.haunt.reconcile import McpCalendarClient
from fateforger.slack_bot.planning_ids import planning_event_id_for_user


async def main():
    user_id = "U095637NL8P"  # Hugo's Slack user ID
    expected_event_id = planning_event_id_for_user(user_id)

    print(f"User ID: {user_id}")
    print(f"Expected planning event ID: {expected_event_id}")
    print()

    mcp_url = os.getenv("CALENDAR_MCP_URL", "http://localhost:3000/mcp")
    print(f"MCP URL: {mcp_url}")

    client = McpCalendarClient(server_url=mcp_url, timeout=10.0)

    # Check if the specific event exists
    print("\n--- Checking for specific planning event by ID ---")
    event = await client.get_event(calendar_id="primary", event_id=expected_event_id)
    if event:
        print(f"✅ Found event: {event.get('summary')} at {event.get('start')}")
    else:
        print("❌ Event not found by ID")

    # List all events in next 24 hours
    print("\n--- Listing all events in next 24 hours ---")
    now = datetime.now(timezone.utc)
    events = await client.list_events(
        calendar_id="primary",
        time_min=now.isoformat(),
        time_max=(now + timedelta(hours=24)).isoformat(),
    )

    if not events:
        print("No events found")
    else:
        for ev in events:
            event_id = ev.get("id", "")
            summary = ev.get("summary", "(no title)")
            start = ev.get("start", {})
            color_id = ev.get("colorId", "")
            start_str = start.get("dateTime") or start.get("date") or "?"
            match = "✅ MATCH" if event_id == expected_event_id else ""
            print(
                f"  - [{event_id[:20]}...] {summary} @ {start_str} (color={color_id}) {match}"
            )


if __name__ == "__main__":
    asyncio.run(main())
if __name__ == "__main__":
    asyncio.run(main())
if __name__ == "__main__":
    asyncio.run(main())
if __name__ == "__main__":
    asyncio.run(main())
if __name__ == "__main__":
    asyncio.run(main())
if __name__ == "__main__":
    asyncio.run(main())
