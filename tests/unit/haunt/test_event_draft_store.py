from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.haunt.event_draft_store import (
    DraftStatus,
    SqlAlchemyEventDraftStore,
    ensure_event_draft_schema,
)


@pytest.mark.asyncio
async def test_event_draft_store_clears_last_error_on_success_status():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_event_draft_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyEventDraftStore(sessionmaker)

        draft = await store.create(
            draft_id="draft_abc123",
            user_id="U1",
            channel_id="D1",
            calendar_id="primary",
            event_id="evt-1",
            title="Daily planning session",
            description="Plan tomorrow.",
            timezone="Europe/Amsterdam",
            start_at_utc=datetime(2026, 1, 18, 9, 0, tzinfo=timezone.utc).isoformat(),
            duration_min=30,
        )

        failed = await store.update_status(
            draft_id=draft.draft_id,
            status=DraftStatus.FAILURE,
            last_error="calendar upsert failed",
        )
        assert failed is not None
        assert failed.last_error == "calendar upsert failed"

        succeeded = await store.update_status(
            draft_id=draft.draft_id,
            status=DraftStatus.SUCCESS,
            event_url="https://www.google.com/calendar/event?eid=abc",
            last_error=None,
        )
        assert succeeded is not None
        assert succeeded.status == DraftStatus.SUCCESS
        assert succeeded.last_error is None
    finally:
        await engine.dispose()
