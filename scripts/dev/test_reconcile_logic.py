#!/usr/bin/env python3
"""
Test the reconcile logic directly and print what jobs would be scheduled.
This simulates what PlanningGuardian.reconcile_all() does.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv()


async def main():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from fateforger.haunt.planning_store import SqlAlchemyPlanningAnchorStore
    from fateforger.haunt.reconcile import (
        McpCalendarClient,
        PlanningRuleConfig,
        PlanningSessionRule,
    )

    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("sqlite://"):
        database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    engine = create_async_engine(database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    store = SqlAlchemyPlanningAnchorStore(sessionmaker)

    mcp_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
    calendar_client = McpCalendarClient(server_url=mcp_url)
    rule = PlanningSessionRule(calendar_client=calendar_client)

    anchors = await store.list_all()
    if not anchors:
        print("No anchors registered")
        return

    now = datetime.now(timezone.utc)
    print(f"⏰ Current time (UTC): {now.isoformat()}")
    print()

    for anchor in anchors:
        print(f"👤 Evaluating for user: {anchor.user_id}")
        desired_jobs = await rule.evaluate(
            now=now,
            scope=anchor.user_id,
            user_id=anchor.user_id,
            channel_id=anchor.channel_id,
            planning_event_id=anchor.event_id,
        )

        if not desired_jobs:
            print("   ✅ No nudges needed (planning session exists)")
        else:
            print(f"   📋 {len(desired_jobs)} jobs would be scheduled:")
            for job in desired_jobs:
                print(f"      • {job.key.kind} at {job.run_at.isoformat()}")
                print(f"        Message: {job.payload.message[:60]}...")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
