from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

Base = declarative_base()


class DraftStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class EventDraft(Base):
    __tablename__ = "event_drafts"
    __table_args__ = (
        UniqueConstraint("draft_id", name="uq_event_drafts_draft_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    message_ts: Mapped[str | None] = mapped_column(String, nullable=True)

    calendar_id: Mapped[str] = mapped_column(String, nullable=False, default="primary")
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="Europe/Amsterdam")

    start_at_utc: Mapped[str] = mapped_column(String, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    status: Mapped[DraftStatus] = mapped_column(String, nullable=False, default=DraftStatus.DRAFT.value)
    event_url: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


@dataclass(frozen=True)
class EventDraftPayload:
    draft_id: str
    user_id: str
    channel_id: str
    message_ts: str | None
    calendar_id: str
    event_id: str
    title: str
    description: str | None
    timezone: str
    start_at_utc: str
    duration_min: int
    status: DraftStatus
    event_url: str | None
    last_error: str | None


class SqlAlchemyEventDraftStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self,
        *,
        draft_id: str,
        user_id: str,
        channel_id: str,
        calendar_id: str,
        event_id: str,
        title: str,
        description: str | None,
        timezone: str,
        start_at_utc: str,
        duration_min: int,
    ) -> EventDraftPayload:
        async with self._sessionmaker() as session:
            row = EventDraft(
                draft_id=draft_id,
                user_id=user_id,
                channel_id=channel_id,
                calendar_id=calendar_id,
                event_id=event_id,
                title=title,
                description=description,
                timezone=timezone,
                start_at_utc=start_at_utc,
                duration_min=duration_min,
                status=DraftStatus.DRAFT.value,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_payload(row)

    async def get_by_draft_id(self, *, draft_id: str) -> Optional[EventDraftPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(EventDraft).where(EventDraft.draft_id == draft_id)
            )
            row = result.scalar_one_or_none()
            return _to_payload(row) if row else None

    async def get_by_message(self, *, channel_id: str, message_ts: str) -> Optional[EventDraftPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(EventDraft).where(
                    EventDraft.channel_id == channel_id, EventDraft.message_ts == message_ts
                )
            )
            row = result.scalar_one_or_none()
            return _to_payload(row) if row else None

    async def attach_message(
        self, *, draft_id: str, channel_id: str, message_ts: str
    ) -> Optional[EventDraftPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(EventDraft).where(EventDraft.draft_id == draft_id)
            )
            row = result.scalar_one_or_none()
            if not row:
                return None
            row.channel_id = channel_id
            row.message_ts = message_ts
            row.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
            return _to_payload(row)

    async def update_time(
        self, *, channel_id: str, message_ts: str, start_at_utc: str | None = None, duration_min: int | None = None
    ) -> Optional[EventDraftPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(EventDraft).where(
                    EventDraft.channel_id == channel_id, EventDraft.message_ts == message_ts
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return None
            if start_at_utc is not None:
                row.start_at_utc = start_at_utc
            if duration_min is not None:
                row.duration_min = duration_min
            row.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
            return _to_payload(row)

    async def update_status(
        self,
        *,
        draft_id: str,
        status: DraftStatus,
        event_url: str | None = None,
        last_error: str | None = None,
    ) -> Optional[EventDraftPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(EventDraft).where(EventDraft.draft_id == draft_id)
            )
            row = result.scalar_one_or_none()
            if not row:
                return None
            row.status = status.value
            if event_url is not None:
                row.event_url = event_url
            if last_error is not None:
                row.last_error = last_error
            row.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
            return _to_payload(row)


async def ensure_event_draft_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: EventDraft.__table__.create(sync_conn, checkfirst=True))


def _to_payload(row: EventDraft) -> EventDraftPayload:
    return EventDraftPayload(
        draft_id=row.draft_id,
        user_id=row.user_id,
        channel_id=row.channel_id,
        message_ts=row.message_ts,
        calendar_id=row.calendar_id,
        event_id=row.event_id,
        title=row.title,
        description=row.description,
        timezone=row.timezone,
        start_at_utc=row.start_at_utc,
        duration_min=row.duration_min,
        status=DraftStatus(row.status),
        event_url=row.event_url,
        last_error=row.last_error,
    )


__all__ = [
    "DraftStatus",
    "EventDraftPayload",
    "SqlAlchemyEventDraftStore",
    "ensure_event_draft_schema",
]

