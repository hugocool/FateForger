"""Constraint models and persistence for timeboxing preferences."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field as PydanticField
from sqlalchemy import Column
from sqlalchemy import DateTime as SQLDateTime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.types import JSON as SAJSON
from sqlmodel import Field, SQLModel


class ConstraintNecessity(str, Enum):
    MUST = "must"
    SHOULD = "should"
    PREFER = "prefer"


class ConstraintStatus(str, Enum):
    PROPOSED = "proposed"
    LOCKED = "locked"
    DECLINED = "declined"


class ConstraintSource(str, Enum):
    USER = "user"
    CALENDAR = "calendar"
    SYSTEM = "system"
    FEEDBACK = "feedback"


class ConstraintScope(str, Enum):
    SESSION = "session"
    PROFILE = "profile"
    DATESPAN = "datespan"


class ConstraintDayOfWeek(str, Enum):
    MO = "MO"
    TU = "TU"
    WE = "WE"
    TH = "TH"
    FR = "FR"
    SA = "SA"
    SU = "SU"


class ConstraintBase(SQLModel):
    name: str
    description: str
    necessity: ConstraintNecessity
    tags: List[str] = Field(default_factory=list, sa_column=Column(SAJSON))
    hints: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(SAJSON))
    status: Optional[ConstraintStatus] = None
    source: Optional[ConstraintSource] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scope: Optional[ConstraintScope] = None
    rationale: Optional[str] = None
    supersedes: List[str] = Field(default_factory=list, sa_column=Column(SAJSON))
    selector: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(SAJSON))
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    days_of_week: List[ConstraintDayOfWeek] = Field(
        default_factory=list, sa_column=Column(SAJSON)
    )
    timezone: Optional[str] = None
    recurrence: Optional[str] = None
    ttl_days: Optional[int] = Field(default=None, ge=1)


class Constraint(ConstraintBase, table=True):
    __tablename__ = "timeboxing_constraints"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    channel_id: Optional[str] = None
    thread_ts: Optional[str] = None
    created_at: datetime = Field(
        sa_column=Column(SQLDateTime, default=datetime.utcnow, nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=Column(
            SQLDateTime,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
            nullable=False,
        )
    )


class ConstraintBatch(PydanticBaseModel):
    constraints: List[ConstraintBase] = PydanticField(default_factory=list)
    notes: Optional[str] = None


class ConstraintStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        """Create a constraint store backed by an async SQLAlchemy sessionmaker."""
        self._sessionmaker = sessionmaker

    async def add_constraints(
        self,
        *,
        user_id: str,
        channel_id: Optional[str],
        thread_ts: Optional[str],
        constraints: List[ConstraintBase],
    ) -> List[Constraint]:
        """Persist a batch of constraints for a user/thread."""
        if not constraints:
            return []
        rows = [
            _constraint_row(
                constraint,
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
            )
            for constraint in constraints
        ]
        async with self._sessionmaker() as session:
            session.add_all(rows)
            await session.commit()
            for row in rows:
                await session.refresh(row)
        return rows

    async def list_constraints(
        self,
        *,
        user_id: str,
        channel_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
        status: Optional[ConstraintStatus] = None,
        scope: Optional[ConstraintScope] = None,
    ) -> List[Constraint]:
        """List constraints for a user with optional filters."""
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(Constraint.user_id == user_id)
            if channel_id:
                stmt = stmt.where(Constraint.channel_id == channel_id)
            if thread_ts:
                stmt = stmt.where(Constraint.thread_ts == thread_ts)
            if status:
                stmt = stmt.where(Constraint.status == status)
            if scope:
                stmt = stmt.where(Constraint.scope == scope)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_constraint(
        self,
        *,
        user_id: str,
        constraint_id: int,
    ) -> Optional[Constraint]:
        """Fetch a single constraint by id for the given user."""
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(
                Constraint.user_id == user_id, Constraint.id == constraint_id
            )
            result = await session.execute(stmt)
            return result.scalars().first()

    async def update_constraint_statuses(
        self,
        *,
        user_id: str,
        decisions: Dict[int, ConstraintStatus],
    ) -> List[Constraint]:
        """Bulk update constraint statuses for a user."""
        if not decisions:
            return []
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(
                Constraint.user_id == user_id, Constraint.id.in_(decisions.keys())
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            for row in rows:
                decision = decisions.get(row.id)
                if decision:
                    row.status = decision
            await session.commit()
            for row in rows:
                await session.refresh(row)
            return rows

    async def update_constraint(
        self,
        *,
        user_id: str,
        constraint_id: int,
        status: Optional[ConstraintStatus] = None,
        description: Optional[str] = None,
    ) -> Optional[Constraint]:
        """Update a single constraint's status or description."""
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(
                Constraint.user_id == user_id, Constraint.id == constraint_id
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            if status is not None:
                row.status = status
            if description is not None:
                row.description = description
            await session.commit()
            await session.refresh(row)
            return row


async def ensure_constraint_schema(engine: AsyncEngine) -> None:
    """Ensure the constraint table exists in the configured database."""
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Constraint.__table__.create(sync_conn, checkfirst=True)
        )


def _constraint_row(
    constraint: ConstraintBase,
    *,
    user_id: str,
    channel_id: Optional[str],
    thread_ts: Optional[str],
) -> Constraint:
    """Convert a ConstraintBase into a persisted Constraint row."""
    payload = constraint.model_dump()
    if payload.get("status") is None:
        payload["status"] = ConstraintStatus.PROPOSED
    if payload.get("source") is None:
        payload["source"] = ConstraintSource.USER
    if payload.get("scope") is None:
        payload["scope"] = ConstraintScope.SESSION
    return Constraint(
        **payload,
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )


__all__ = [
    "Constraint",
    "ConstraintBase",
    "ConstraintBatch",
    "ConstraintDayOfWeek",
    "ConstraintNecessity",
    "ConstraintScope",
    "ConstraintSource",
    "ConstraintStatus",
    "ConstraintStore",
    "ensure_constraint_schema",
]
