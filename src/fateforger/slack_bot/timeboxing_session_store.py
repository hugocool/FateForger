"""Restart-safe SQL persistence for adaptive timeboxing sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Date, DateTime, Integer, String, Text, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    PlanningSessionRepository,
    StaleSessionRevision,
    TimeboxingStanding,
)
from fateforger.agents.timeboxing.session_contracts import (
    HandledInteraction,
    PlanningSessionSnapshot,
    TurnOutcome,
)


class _Base(DeclarativeBase):
    """Declarative base local to the adaptive-session persistence adapter."""


class _TimeboxingSessionState(_Base):
    """One current snapshot and replay envelope per Slack planning session."""

    __tablename__ = "timeboxing_session_states"

    session_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    planning_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _StoredSessionEnvelope(BaseModel):
    """Versioned atomic payload for current state and interaction replay."""

    model_config = ConfigDict(extra="forbid", strict=True)

    envelope_version: Literal[1] = 1
    snapshot: PlanningSessionSnapshot
    outcomes: dict[str, TurnOutcome]


class SqlAlchemyTimeboxingSessionRepository(PlanningSessionRepository):
    """Persist adaptive sessions with SQL CAS and process-local coalescing."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        """Bind the adapter to a migrated async SQLAlchemy database."""

        self._sessionmaker = sessionmaker
        self._session_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def session_guard(self, session_key: str) -> AsyncIterator[None]:
        """Coalesce one session only within this repository process instance."""

        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            yield

    async def load(self, session_key: str) -> PlanningSessionSnapshot | None:
        """Read a session without establishing one; the seam's question."""

        async with self._sessionmaker() as session:
            row = await self._load_row(session, session_key)
            if row is None:
                return None
            return self._parse_envelope(row.snapshot_json).snapshot.model_copy(
                deep=True
            )

    async def load_or_create(
        self, session_key: str, *, owner_user_id: str
    ) -> PlanningSessionSnapshot:
        """Load a session or atomically establish its revision-zero envelope."""

        async with self._sessionmaker() as session:
            row = await self._load_row(session, session_key)
            if row is not None:
                return self._parse_envelope(row.snapshot_json).snapshot.model_copy(
                    deep=True
                )

            snapshot = PlanningSessionSnapshot.new(
                session_key=session_key, owner_user_id=owner_user_id
            )
            now = datetime.now(UTC)
            session.add(
                _TimeboxingSessionState(
                    session_key=session_key,
                    owner_user_id=owner_user_id,
                    revision=0,
                    status=snapshot.status,
                    planning_date=None,
                    snapshot_json=self._serialize_envelope(
                        _StoredSessionEnvelope(snapshot=snapshot, outcomes={})
                    ),
                    created_at=now,
                    updated_at=now,
                )
            )
            try:
                await session.commit()
                return snapshot.model_copy(deep=True)
            except IntegrityError:
                await session.rollback()
                row = await self._load_row(session, session_key)
                if row is None:
                    raise
                return self._parse_envelope(row.snapshot_json).snapshot.model_copy(
                    deep=True
                )

    async def load_outcome(
        self, session_key: str, *, interaction_id: str
    ) -> TurnOutcome | None:
        """Load one complete typed outcome for duplicate or restart replay."""

        async with self._sessionmaker() as session:
            row = await self._load_row(session, session_key)
            if row is None:
                return None
            outcome = self._parse_envelope(row.snapshot_json).outcomes.get(
                interaction_id
            )
            return outcome.model_copy(deep=True) if outcome is not None else None

    async def save(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        expected_revision: int,
        interaction_id: str,
        outcome: TurnOutcome,
    ) -> PlanningSessionSnapshot:
        """Atomically save state and outcome when the SQL revision still matches."""

        async with self._sessionmaker() as session:
            row = await self._load_row(session, snapshot.session_key)
            if row is None or row.revision != expected_revision:
                raise StaleSessionRevision

            current = self._parse_envelope(row.snapshot_json)
            if interaction_id in current.outcomes:
                return current.snapshot.model_copy(deep=True)

            next_revision = expected_revision + 1
            saved = PlanningSessionSnapshot.model_validate(
                {
                    **snapshot.model_dump(mode="python"),
                    "revision": next_revision,
                    "handled_interactions": [
                        *snapshot.handled_interactions,
                        HandledInteraction(
                            interaction_id=interaction_id,
                            outcome_kind=outcome.kind,
                            session_revision=next_revision,
                        ),
                    ],
                }
            )
            envelope = _StoredSessionEnvelope(
                snapshot=saved,
                outcomes={**current.outcomes, interaction_id: outcome},
            )
            result = await session.execute(
                update(_TimeboxingSessionState)
                .where(
                    _TimeboxingSessionState.session_key == snapshot.session_key,
                    _TimeboxingSessionState.revision == expected_revision,
                )
                .values(
                    owner_user_id=saved.owner_user_id,
                    revision=next_revision,
                    status=saved.status,
                    planning_date=(
                        saved.planning_day.date
                        if saved.planning_day is not None
                        else None
                    ),
                    snapshot_json=self._serialize_envelope(envelope),
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise StaleSessionRevision
            await session.commit()
            return saved.model_copy(deep=True)

    async def standing_for(
        self,
        *,
        owner_user_id: str,
        open_since: datetime,
        planned_from: date,
        planned_to: date,
    ) -> TimeboxingStanding:
        """One query over the indexed columns; the snapshot JSON is never read.

        ``updated_at`` is written naive in UTC by ``save``, so the bound is
        compared the same way.
        """

        since = open_since.astimezone(UTC).replace(tzinfo=None)
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(
                    _TimeboxingSessionState.session_key,
                    _TimeboxingSessionState.status,
                )
                .where(
                    _TimeboxingSessionState.owner_user_id == owner_user_id,
                    or_(
                        (_TimeboxingSessionState.status == "open")
                        & (_TimeboxingSessionState.updated_at >= since),
                        (_TimeboxingSessionState.status == "committed")
                        & (_TimeboxingSessionState.planning_date >= planned_from)
                        & (_TimeboxingSessionState.planning_date <= planned_to),
                    ),
                )
                .order_by(_TimeboxingSessionState.updated_at.desc())
            )
            rows = result.all()
        open_key = next((key for key, status in rows if status == "open"), None)
        committed_key = next(
            (key for key, status in rows if status == "committed"), None
        )
        return TimeboxingStanding(
            open_session_key=open_key, committed_session_key=committed_key
        )

    @staticmethod
    async def _load_row(
        session: AsyncSession, session_key: str
    ) -> _TimeboxingSessionState | None:
        """Load the single persisted row for a session key."""

        result = await session.execute(
            select(_TimeboxingSessionState).where(
                _TimeboxingSessionState.session_key == session_key
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _parse_envelope(payload: str) -> _StoredSessionEnvelope:
        """Validate snapshot and discriminated typed outcomes from storage."""

        return _StoredSessionEnvelope.model_validate_json(payload)

    @staticmethod
    def _serialize_envelope(envelope: _StoredSessionEnvelope) -> str:
        """Serialize one validated envelope for its atomic SQL write."""

        return envelope.model_dump_json()


__all__ = ["SqlAlchemyTimeboxingSessionRepository"]
