from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

Base = declarative_base()


class PlanningSessionStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlanningSessionRef(Base):
    __tablename__ = "planning_session_refs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "planned_date", name="uq_planning_session_ref_user_date"
        ),
        UniqueConstraint(
            "calendar_id", "event_id", name="uq_planning_session_ref_calendar_event"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    calendar_id: Mapped[str] = mapped_column(String, nullable=False, default="primary")
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default=PlanningSessionStatus.PLANNED.value
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    event_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    thread_ts: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


@dataclass(frozen=True)
class PlanningSessionRefPayload:
    user_id: str
    planned_date: date
    calendar_id: str
    event_id: str
    status: str
    title: str | None
    event_url: str | None
    source: str | None
    channel_id: str | None
    thread_ts: str | None


class SqlAlchemyPlanningSessionStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def upsert(
        self,
        *,
        user_id: str,
        planned_date: date,
        calendar_id: str,
        event_id: str,
        status: PlanningSessionStatus | str,
        title: str | None = None,
        event_url: str | None = None,
        source: str | None = None,
        channel_id: str | None = None,
        thread_ts: str | None = None,
    ) -> PlanningSessionRefPayload:
        status_value = _status_value(status)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(PlanningSessionRef).where(
                    PlanningSessionRef.user_id == user_id,
                    PlanningSessionRef.planned_date == planned_date,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = PlanningSessionRef(
                    user_id=user_id,
                    planned_date=planned_date,
                    calendar_id=calendar_id,
                    event_id=event_id,
                    status=status_value,
                    title=title,
                    event_url=event_url,
                    source=source,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                )
                session.add(row)
            else:
                row.calendar_id = calendar_id
                row.event_id = event_id
                row.status = status_value
                row.title = title
                row.event_url = event_url
                row.source = source
                row.channel_id = channel_id
                row.thread_ts = thread_ts
                row.updated_at = datetime.utcnow()

            await session.commit()
            await session.refresh(row)
            return _to_payload(row)

    async def list_for_user_between(
        self,
        *,
        user_id: str,
        start_date: date,
        end_date: date,
        statuses: tuple[PlanningSessionStatus | str, ...] = (
            PlanningSessionStatus.PLANNED,
            PlanningSessionStatus.IN_PROGRESS,
        ),
    ) -> list[PlanningSessionRefPayload]:
        allowed = tuple(_status_value(status) for status in statuses)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(PlanningSessionRef).where(
                    PlanningSessionRef.user_id == user_id,
                    PlanningSessionRef.planned_date >= start_date,
                    PlanningSessionRef.planned_date <= end_date,
                    PlanningSessionRef.status.in_(allowed),
                )
            )
            return [_to_payload(row) for row in result.scalars().all()]

    async def get_by_event_id(
        self, *, calendar_id: str, event_id: str
    ) -> Optional[PlanningSessionRefPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(PlanningSessionRef).where(
                    PlanningSessionRef.calendar_id == calendar_id,
                    PlanningSessionRef.event_id == event_id,
                )
            )
            row = result.scalar_one_or_none()
            return _to_payload(row) if row else None


def _status_value(status: PlanningSessionStatus | str) -> str:
    if isinstance(status, PlanningSessionStatus):
        return status.value
    return str(status).strip().lower()


async def ensure_planning_session_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: PlanningSessionRef.__table__.create(
                sync_conn, checkfirst=True
            )
        )


def _to_payload(row: PlanningSessionRef) -> PlanningSessionRefPayload:
    return PlanningSessionRefPayload(
        user_id=row.user_id,
        planned_date=row.planned_date,
        calendar_id=row.calendar_id,
        event_id=row.event_id,
        status=row.status,
        title=row.title,
        event_url=row.event_url,
        source=row.source,
        channel_id=row.channel_id,
        thread_ts=row.thread_ts,
    )


__all__ = [
    "PlanningSessionRefPayload",
    "PlanningSessionRef",
    "PlanningSessionStatus",
    "SqlAlchemyPlanningSessionStore",
    "ensure_planning_session_schema",
]
