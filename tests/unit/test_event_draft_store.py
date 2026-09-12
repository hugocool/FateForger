from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.haunt.event_draft_store import (
    DraftStatus,
    SqlAlchemyEventDraftStore,
    ensure_event_draft_schema,
)


@pytest.mark.asyncio
async def test_update_time_by_draft_id_writes_against_the_real_store():
    """The live write path, against SQLAlchemy rather than a fake.

    The card is posted to the DM and copied to #admonishments, so the clicked
    message's coordinates are not a reliable key — the draft's own id is. This
    asserts the row really moves and really reads back.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_event_draft_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        store = SqlAlchemyEventDraftStore(sessionmaker)

        proposed = datetime(2026, 9, 11, 15, 29, tzinfo=timezone.utc)
        picked = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)
        draft = await store.create(
            draft_id="draft_abc123",
            user_id="U1",
            channel_id="D1",
            calendar_id="primary",
            event_id="evt-1",
            title="Daily planning session",
            description="Plan tomorrow.",
            timezone="Europe/Amsterdam",
            start_at_utc=proposed.isoformat(),
            duration_min=30,
        )
        # The row has never been told where its card was posted, which is the
        # state a click on the log copy arrives in.
        assert draft.message_ts is None

        moved = await store.update_time_by_draft_id(
            draft_id="draft_abc123",
            start_at_utc=picked.isoformat(),
            duration_min=45,
        )
        assert moved is not None
        assert moved.start_at_utc == picked.isoformat()
        assert moved.duration_min == 45

        read_back = await store.get_by_draft_id(draft_id="draft_abc123")
        assert read_back is not None
        assert read_back.start_at_utc == picked.isoformat()
        assert read_back.duration_min == 45

        assert (
            await store.update_time_by_draft_id(
                draft_id="draft_nope", start_at_utc=picked.isoformat()
            )
            is None
        )
    finally:
        await engine.dispose()


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
