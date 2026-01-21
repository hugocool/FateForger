#!/usr/bin/env python3
"""Check if the PlanningAnchor's event_id exists in the calendar."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv()


async def main():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from fateforger.haunt.planning_store import SqlAlchemyPlanningAnchorStore
    from fateforger.haunt.reconcile import McpCalendarClient

    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("sqlite://"):
        database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    engine = create_async_engine(database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlAlchemyPlanningAnchorStore(sessionmaker)

    anchors = await store.list_all()
    if not anchors:
        print("No anchors found")
        return

    mcp_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
    calendar_client = McpCalendarClient(server_url=mcp_url)

    for anchor in anchors:
        print(f"\n👤 User: {anchor.user_id}")
        print(f"   Event ID: {anchor.event_id}")

        event = await calendar_client.get_event(
            calendar_id="primary", event_id=anchor.event_id
        )
        if event:
            print(f"   ✅ Event EXISTS in calendar!")
            print(f"      Summary: {event.get('summary')}")
            print(f"      Start: {event.get('start')}")
            print("   → Since the anchor event exists, NO nudges will be scheduled!")
        else:
            print("   ❌ Event does NOT exist in calendar")
            print("      → You SHOULD be getting nudges!")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
