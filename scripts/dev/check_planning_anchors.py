#!/usr/bin/env python3
"""Diagnostic script to check PlanningAnchor registrations and Guardian status."""

import asyncio
import os
import sys
from datetime import datetime, timezone

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv()


async def main():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from fateforger.haunt.planning_store import (
        SqlAlchemyPlanningAnchorStore,
        ensure_planning_anchor_schema,
    )
    from fateforger.haunt.reconcile import McpCalendarClient, PlanningRuleConfig

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("❌ DATABASE_URL not set")
        return

    # Convert to async URL
    if database_url.startswith("sqlite://"):
        database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    print(f"📦 Database: {database_url[:50]}...")

    engine = create_async_engine(database_url)
    await ensure_planning_anchor_schema(engine)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlAlchemyPlanningAnchorStore(sessionmaker)

    # List all anchors
    anchors = await store.list_all()
    print(f"\n📋 PlanningAnchors registered: {len(anchors)}")
    if not anchors:
        print("   ❌ No users registered for planning nudges!")
        print("   → DM the bot or mention it to register yourself")
    else:
        for anchor in anchors:
            print(f"   • User: {anchor.user_id}")
            print(f"     Channel: {anchor.channel_id}")
            print(f"     Event ID: {anchor.event_id}")
            print()

    # Check MCP calendar connectivity
    mcp_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
    print(f"\n🗓️  Checking Calendar MCP at {mcp_url}...")
    try:
        calendar_client = McpCalendarClient(server_url=mcp_url)
        now = datetime.now(timezone.utc)
        config = PlanningRuleConfig()
        events = await calendar_client.list_events(
            calendar_id="primary",
            time_min=now.isoformat(),
            time_max=(now + config.horizon).isoformat(),
        )
        print(f"   ✅ Connected! Found {len(events)} events in next 24h")

        # Check if any are planning events
        planning_events = [
            e
            for e in events
            if any(
                kw in (e.get("summary") or "").lower() for kw in config.summary_keywords
            )
        ]
        if planning_events:
            print(f"   📅 Planning sessions found: {len(planning_events)}")
            for e in planning_events[:3]:
                print(
                    f"      - {e.get('summary')} @ {e.get('start', {}).get('dateTime', 'N/A')}"
                )
        else:
            print("   ⚠️  No planning sessions found in next 24h")
            print("      → If you have an anchor, you SHOULD get nudged")

    except Exception as ex:
        print(f"   ❌ Failed to connect: {ex}")
        print("      → Planning reconciler may be disabled!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
