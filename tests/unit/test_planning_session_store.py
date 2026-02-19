from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.haunt.planning_session_store import (
    PlanningSessionStatus,
    SqlAlchemyPlanningSessionStore,
    ensure_planning_session_schema,
)


@pytest.mark.asyncio
async def test_planning_session_store_upsert_and_lookup_with_string_status():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_planning_session_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyPlanningSessionStore(sessionmaker)

        await store.upsert(
            user_id="U1",
            planned_date=date(2025, 1, 1),
            calendar_id="primary",
            event_id="evt-1",
            status="planned",
            title="Daily planning session",
            event_url="https://calendar.google.com/event?eid=abc",
            source="test",
            channel_id="D1",
            thread_ts="123.456",
        )

        rows = await store.list_for_user_between(
            user_id="U1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 1),
            statuses=("planned",),
        )
        assert len(rows) == 1
        assert rows[0].event_id == "evt-1"
        assert rows[0].status == "planned"

        by_event = await store.get_by_event_id(calendar_id="primary", event_id="evt-1")
        assert by_event is not None
        assert by_event.event_url == "https://calendar.google.com/event?eid=abc"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_planning_session_store_upsert_updates_existing_user_day():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_planning_session_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyPlanningSessionStore(sessionmaker)

        await store.upsert(
            user_id="U1",
            planned_date=date(2025, 1, 1),
            calendar_id="primary",
            event_id="evt-1",
            status=PlanningSessionStatus.PLANNED,
        )
        await store.upsert(
            user_id="U1",
            planned_date=date(2025, 1, 1),
            calendar_id="primary",
            event_id="evt-2",
            status=PlanningSessionStatus.IN_PROGRESS,
        )

        rows = await store.list_for_user_between(
            user_id="U1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 1),
            statuses=(
                PlanningSessionStatus.PLANNED,
                PlanningSessionStatus.IN_PROGRESS,
            ),
        )
        assert len(rows) == 1
        assert rows[0].event_id == "evt-2"
        assert rows[0].status == "in_progress"
    finally:
        await engine.dispose()
