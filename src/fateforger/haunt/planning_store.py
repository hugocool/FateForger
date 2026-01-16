from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

Base = declarative_base()


class PlanningAnchor(Base):
    __tablename__ = "planning_anchors"
    __table_args__ = (UniqueConstraint("user_id", name="uq_planning_anchor_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    calendar_id: Mapped[str] = mapped_column(String, nullable=False, default="primary")
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


@dataclass(frozen=True)
class PlanningAnchorPayload:
    user_id: str
    channel_id: str | None
    calendar_id: str
    event_id: str


class SqlAlchemyPlanningAnchorStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, *, user_id: str) -> Optional[PlanningAnchorPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(PlanningAnchor).where(PlanningAnchor.user_id == user_id)
            )
            row = result.scalar_one_or_none()
            return _to_payload(row) if row else None

    async def upsert(
        self,
        *,
        user_id: str,
        channel_id: str | None,
        calendar_id: str,
        event_id: str,
    ) -> PlanningAnchorPayload:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(PlanningAnchor).where(PlanningAnchor.user_id == user_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = PlanningAnchor(
                    user_id=user_id,
                    channel_id=channel_id,
                    calendar_id=calendar_id,
                    event_id=event_id,
                )
                session.add(row)
            else:
                row.channel_id = channel_id
                row.calendar_id = calendar_id
                row.event_id = event_id
                row.updated_at = datetime.utcnow()

            await session.commit()
            await session.refresh(row)
            return _to_payload(row)

    async def get_by_event_id(self, *, event_id: str) -> Optional[PlanningAnchorPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(PlanningAnchor).where(PlanningAnchor.event_id == event_id)
            )
            row = result.scalar_one_or_none()
            return _to_payload(row) if row else None

    async def list_all(self) -> list[PlanningAnchorPayload]:
        async with self._sessionmaker() as session:
            result = await session.execute(select(PlanningAnchor))
            return [_to_payload(row) for row in result.scalars().all()]


async def ensure_planning_anchor_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: PlanningAnchor.__table__.create(sync_conn, checkfirst=True)
        )


def _to_payload(row: PlanningAnchor) -> PlanningAnchorPayload:
    return PlanningAnchorPayload(
        user_id=row.user_id,
        channel_id=row.channel_id,
        calendar_id=row.calendar_id,
        event_id=row.event_id,
    )


__all__ = [
    "PlanningAnchorPayload",
    "SqlAlchemyPlanningAnchorStore",
    "ensure_planning_anchor_schema",
]
