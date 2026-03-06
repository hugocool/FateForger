"""Constraint models and persistence for timeboxing preferences."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field as PydanticField
from sqlalchemy import Column
from sqlalchemy import DateTime as SQLDateTime
from sqlalchemy import or_, select
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

    async def upsert_constraints(
        self,
        *,
        user_id: str,
        channel_id: Optional[str],
        thread_ts: Optional[str],
        constraints: List[ConstraintBase],
    ) -> Dict[str, int]:
        """Persist constraints while avoiding duplicate shared-scope inserts."""
        if not constraints:
            return {"added": 0, "skipped": 0, "total": 0}
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(Constraint.user_id == user_id)
            if channel_id:
                stmt = stmt.where(Constraint.channel_id == channel_id)
            result = await session.execute(stmt)
            existing_rows = list(result.scalars().all())
            seen_keys = {_constraint_row_identity(row) for row in existing_rows}

            to_add: list[Constraint] = []
            skipped = 0
            for constraint in constraints:
                scope = constraint.scope or ConstraintScope.SESSION
                target_thread_ts = (
                    None
                    if scope in (ConstraintScope.PROFILE, ConstraintScope.DATESPAN)
                    else thread_ts
                )
                row = _constraint_row(
                    constraint,
                    user_id=user_id,
                    channel_id=channel_id,
                    thread_ts=target_thread_ts,
                )
                key = _constraint_row_identity(row)
                if key in seen_keys:
                    skipped += 1
                    continue
                seen_keys.add(key)
                to_add.append(row)

            if to_add:
                session.add_all(to_add)
                await session.commit()
                for row in to_add:
                    await session.refresh(row)
            return {"added": len(to_add), "skipped": skipped, "total": len(constraints)}

    async def prune_shared_constraints(
        self,
        *,
        user_id: str,
        channel_id: Optional[str] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Deduplicate shared-scope constraints by canonical precedence."""
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(
                Constraint.user_id == user_id,
                or_(
                    Constraint.scope == ConstraintScope.PROFILE,
                    Constraint.scope == ConstraintScope.DATESPAN,
                ),
            )
            if channel_id:
                stmt = stmt.where(Constraint.channel_id == channel_id)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            groups: Dict[str, List[Constraint]] = {}
            for row in rows:
                groups.setdefault(_shared_constraint_identity(row), []).append(row)

            canonical_by_group: Dict[str, Constraint] = {}
            duplicate_rows: list[Constraint] = []
            duplicate_groups = 0
            for key, items in groups.items():
                canonical = max(items, key=_shared_canonical_sort_key)
                canonical_by_group[key] = canonical
                duplicates = [row for row in items if row.id != canonical.id]
                if duplicates:
                    duplicate_groups += 1
                    duplicate_rows.extend(duplicates)

            pruned = 0
            if not dry_run and duplicate_rows:
                for row in duplicate_rows:
                    await session.delete(row)
                    pruned += 1
                await session.commit()

            return {
                "dry_run": dry_run,
                "raw_shared_rows": len(rows),
                "canonical_shared_rows": len(canonical_by_group),
                "duplicate_groups": duplicate_groups,
                "duplicates_found": len(duplicate_rows),
                "duplicates_pruned": pruned,
                "canonical_statuses_by_identity": {
                    key: _status_text(value.status)
                    for key, value in canonical_by_group.items()
                },
            }

    async def list_constraints(
        self,
        *,
        user_id: str,
        channel_id: Optional[str] = None,
        thread_ts: Optional[str] = None,
        status: Optional[ConstraintStatus] = None,
        scope: Optional[ConstraintScope] = None,
        include_shared_scopes: bool = False,
    ) -> List[Constraint]:
        """List constraints for a user with optional filters."""
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(Constraint.user_id == user_id)
            if channel_id:
                stmt = stmt.where(Constraint.channel_id == channel_id)
            if thread_ts:
                if include_shared_scopes:
                    stmt = stmt.where(
                        or_(
                            Constraint.thread_ts == thread_ts,
                            Constraint.scope == ConstraintScope.PROFILE,
                            Constraint.scope == ConstraintScope.DATESPAN,
                        )
                    )
                else:
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


def _status_rank(status: Optional[ConstraintStatus]) -> int:
    if status == ConstraintStatus.LOCKED:
        return 3
    if status == ConstraintStatus.PROPOSED:
        return 2
    if status == ConstraintStatus.DECLINED:
        return 1
    return 0


def _status_text(status: Optional[ConstraintStatus]) -> str:
    if isinstance(status, ConstraintStatus):
        return status.value
    return str(status or "")


def _shared_canonical_sort_key(row: Constraint) -> tuple[int, float, float, int]:
    updated = row.updated_at.timestamp() if row.updated_at else 0.0
    created = row.created_at.timestamp() if row.created_at else 0.0
    return (_status_rank(row.status), updated, created, int(row.id or 0))


def _shared_constraint_identity(row: Constraint) -> str:
    return _constraint_row_identity(row, include_thread=False)


def _constraint_row_identity(row: Constraint, *, include_thread: bool = True) -> str:
    hints = row.hints if isinstance(row.hints, dict) else {}
    uid = str(hints.get("uid") or "").strip().lower()
    scope = row.scope if isinstance(row.scope, ConstraintScope) else ConstraintScope.SESSION
    channel = str(row.channel_id or "").strip().lower()
    necessity = (
        row.necessity.value
        if isinstance(row.necessity, ConstraintNecessity)
        else str(row.necessity or "").strip().lower()
    )
    name = str(row.name or "").strip().lower()
    description = str(row.description or "").strip().lower()
    base = uid or "|".join([name, description, necessity, scope.value])
    if include_thread and scope == ConstraintScope.SESSION:
        thread = str(row.thread_ts or "").strip().lower()
        return f"session|{channel}|{thread}|{base}"
    return f"shared|{channel}|{scope.value}|{base}"


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
