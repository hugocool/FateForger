"""Constraint models and persistence for timeboxing preferences."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field as PydanticField
from sqlalchemy import Column
from sqlalchemy import DateTime as SQLDateTime
from sqlalchemy import and_, or_, select
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
        """Persist a batch of constraints for a user/thread.

        Shared scopes (`PROFILE`, `DATESPAN`) are canonicalized across threads to
        avoid local-mirror accumulation.
        """
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
            incoming_shared = [
                row
                for row in rows
                if row.scope in {ConstraintScope.PROFILE, ConstraintScope.DATESPAN}
            ]
            incoming_session = [
                row
                for row in rows
                if row.scope not in {ConstraintScope.PROFILE, ConstraintScope.DATESPAN}
            ]
            persisted: list[Constraint] = []

            if incoming_shared:
                stmt = select(Constraint).where(
                    Constraint.user_id == user_id,
                    Constraint.scope.in_(
                        [ConstraintScope.PROFILE, ConstraintScope.DATESPAN]
                    ),
                )
                result = await session.execute(stmt)
                existing_shared = list(result.scalars().all())
                existing_by_key = _group_constraints_by_semantics(existing_shared)

                for incoming in incoming_shared:
                    key = _constraint_semantic_key(incoming)
                    matches = list(existing_by_key.get(key) or [])
                    if not matches:
                        incoming.channel_id = None
                        incoming.thread_ts = None
                        session.add(incoming)
                        existing_by_key.setdefault(key, []).append(incoming)
                        persisted.append(incoming)
                        continue

                    canonical_existing = _canonical_constraint(matches)
                    canonical_candidate = _canonical_constraint([*matches, incoming])
                    if canonical_candidate is incoming:
                        payload = incoming.model_dump(
                            exclude={
                                "id",
                                "user_id",
                                "channel_id",
                                "thread_ts",
                                "created_at",
                                "updated_at",
                            }
                        )
                        _apply_constraint_payload(canonical_existing, payload)
                    canonical_existing.channel_id = None
                    canonical_existing.thread_ts = None
                    persisted.append(canonical_existing)

            if incoming_session:
                session.add_all(incoming_session)
                persisted.extend(incoming_session)

            await session.commit()
            for row in _dedupe_constraint_rows_by_id(persisted):
                await session.refresh(row)
            return _dedupe_constraint_rows_by_id(persisted)

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
            if include_shared_scopes and thread_ts and channel_id:
                stmt = stmt.where(
                    or_(
                        and_(
                            Constraint.channel_id == channel_id,
                            Constraint.thread_ts == thread_ts,
                        ),
                        Constraint.scope.in_(
                            [ConstraintScope.PROFILE, ConstraintScope.DATESPAN]
                        ),
                    )
                )
            else:
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
            rows = list(result.scalars().all())
            if include_shared_scopes:
                return _canonicalize_shared_rows(rows)
            return rows

    async def shared_scope_stats(self, *, user_id: str) -> dict[str, Any]:
        """Return canonicalization stats for shared local constraints."""
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(
                Constraint.user_id == user_id,
                Constraint.scope.in_([ConstraintScope.PROFILE, ConstraintScope.DATESPAN]),
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
        grouped = _group_constraints_by_semantics(rows)
        duplicate_groups = 0
        duplicates_found = 0
        for group in grouped.values():
            if len(group) <= 1:
                continue
            duplicate_groups += 1
            duplicates_found += max(0, len(group) - 1)
        return {
            "raw_shared_rows": len(rows),
            "canonical_shared_rows": len(grouped),
            "duplicate_groups": duplicate_groups,
            "duplicates_found": duplicates_found,
        }

    async def prune_shared_constraints(
        self,
        *,
        user_id: str,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Archive duplicate shared constraints while keeping one canonical row/key."""
        async with self._sessionmaker() as session:
            stmt = select(Constraint).where(
                Constraint.user_id == user_id,
                Constraint.scope.in_([ConstraintScope.PROFILE, ConstraintScope.DATESPAN]),
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            grouped = _group_constraints_by_semantics(rows)
            duplicate_groups_payload: list[dict[str, Any]] = []
            duplicates_found = 0
            duplicates_archived = 0
            for group_rows in grouped.values():
                if len(group_rows) <= 1:
                    continue
                ranked = sorted(group_rows, key=_constraint_canonical_rank)
                canonical = ranked[0]
                duplicates = ranked[1:]
                duplicate_groups_payload.append(
                    {
                        "canonical_id": canonical.id,
                        "duplicate_ids": [row.id for row in duplicates if row.id],
                    }
                )
                duplicates_found += len(duplicates)
                if dry_run:
                    continue
                for duplicate in duplicates:
                    if duplicate.status != ConstraintStatus.DECLINED:
                        duplicate.status = ConstraintStatus.DECLINED
                        duplicates_archived += 1
                    hints = dict(duplicate.hints or {})
                    hints["pruned_duplicate_of"] = canonical.id
                    hints["pruned_at"] = datetime.utcnow().isoformat()
                    duplicate.hints = hints
            if not dry_run:
                await session.commit()
        return {
            "dry_run": bool(dry_run),
            "raw_shared_rows": len(rows),
            "canonical_shared_rows": len(grouped),
            "duplicate_groups": len(duplicate_groups_payload),
            "duplicates_found": duplicates_found,
            "duplicates_archived": duplicates_archived,
            "groups": duplicate_groups_payload,
        }

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


def _constraint_semantic_key(constraint: Constraint) -> str:
    """Return a stable semantic key for shared local-constraint identity."""
    hints = dict(constraint.hints or {})
    selector = dict(constraint.selector or {})
    rule_kind = str(
        hints.get("rule_kind") or selector.get("rule_kind") or ""
    ).strip().lower()
    tags = sorted(
        str(tag).strip().lower() for tag in (constraint.tags or []) if str(tag).strip()
    )
    days = sorted(
        day.value if isinstance(day, ConstraintDayOfWeek) else str(day)
        for day in (constraint.days_of_week or [])
    )
    return "|".join(
        [
            str(constraint.scope.value if constraint.scope else "").lower(),
            str(constraint.name or "").strip().lower(),
            rule_kind,
            str(constraint.start_date or ""),
            str(constraint.end_date or ""),
            ",".join(days),
            str(constraint.timezone or "").strip().lower(),
            str(constraint.recurrence or "").strip().lower(),
            ",".join(tags),
        ]
    )


def _constraint_canonical_rank(constraint: Constraint) -> tuple[int, float, int]:
    """Rank constraints with required precedence: status then newest timestamp."""
    status_rank = {
        ConstraintStatus.LOCKED: 0,
        ConstraintStatus.PROPOSED: 1,
        ConstraintStatus.DECLINED: 2,
    }
    updated = constraint.updated_at.timestamp() if constraint.updated_at else 0.0
    return (status_rank.get(constraint.status, 3), -updated, -(constraint.id or 0))


def _canonical_constraint(constraints: list[Constraint]) -> Constraint:
    ranked = sorted(constraints, key=_constraint_canonical_rank)
    return ranked[0]


def _apply_constraint_payload(target: Constraint, payload: Dict[str, Any]) -> None:
    """Apply mutable constraint fields on an existing row."""
    mutable_fields = (
        "name",
        "description",
        "necessity",
        "tags",
        "hints",
        "status",
        "source",
        "confidence",
        "scope",
        "rationale",
        "supersedes",
        "selector",
        "start_date",
        "end_date",
        "days_of_week",
        "timezone",
        "recurrence",
        "ttl_days",
    )
    for field in mutable_fields:
        if field in payload:
            setattr(target, field, payload[field])


def _group_constraints_by_semantics(rows: list[Constraint]) -> dict[str, list[Constraint]]:
    grouped: dict[str, list[Constraint]] = {}
    for row in rows:
        grouped.setdefault(_constraint_semantic_key(row), []).append(row)
    return grouped


def _canonicalize_shared_rows(rows: list[Constraint]) -> list[Constraint]:
    """Canonicalize shared rows while preserving all non-shared rows."""
    shared = [
        row
        for row in rows
        if row.scope in {ConstraintScope.PROFILE, ConstraintScope.DATESPAN}
    ]
    non_shared = [
        row
        for row in rows
        if row.scope not in {ConstraintScope.PROFILE, ConstraintScope.DATESPAN}
    ]
    grouped = _group_constraints_by_semantics(shared)
    canonical_shared = [_canonical_constraint(group) for group in grouped.values()]
    return [*non_shared, *canonical_shared]


def _dedupe_constraint_rows_by_id(rows: list[Constraint]) -> list[Constraint]:
    deduped: list[Constraint] = []
    seen: set[int] = set()
    for row in rows:
        row_id = int(row.id or 0)
        if row_id and row_id in seen:
            continue
        if row_id:
            seen.add(row_id)
        deduped.append(row)
    return deduped


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
