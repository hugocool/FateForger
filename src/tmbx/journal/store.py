# src/tmbx/journal/store.py
"""Async SQLite store for journal rows."""

from __future__ import annotations

from datetime import date as date_type
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from .models import JournalEntry

DEFAULT_JOURNAL_PATH = Path("data/tmbx_journal.db")


def journal_sessionmaker(
    db_path: Path | str = DEFAULT_JOURNAL_PATH,
) -> async_sessionmaker[AsyncSession]:
    """Build a sessionmaker without creating the schema or touching the loop.

    ``create_async_engine`` connects lazily, so this is safe to call from a
    synchronous constructor even while an event loop is running — which is
    exactly the situation in the Slack bot. Never call ``asyncio.run`` or
    ``run_until_complete`` from there; both raise inside a running loop, and a
    swallowed exception would leave the journal silently disabled.

    The schema must already exist. Create it with ``init_journal``.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_journal(
    db_path: Path | str = DEFAULT_JOURNAL_PATH,
) -> async_sessionmaker[AsyncSession]:
    """Create the journal schema and return a sessionmaker.

    Explicit entrypoint — call it from an async server startup or the
    ``tmbx-init-journal`` command, never from a write path. Repo policy
    forbids runtime table creation in live paths.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all, tables=[JournalEntry.__table__])
    return async_sessionmaker(engine, expire_on_commit=False)


def init_journal_cli() -> None:
    """``tmbx-init-journal`` entrypoint — create the schema, then exit."""
    import asyncio

    asyncio.run(init_journal())
    print(f"journal ready at {DEFAULT_JOURNAL_PATH}")


class JournalStore:
    """Append-only reader/writer over ``tmbx_journal``."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def append(self, entry: JournalEntry) -> int:
        """Persist one row and return its id."""
        async with self._sessionmaker() as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            assert entry.id is not None
            return entry.id

    async def get(self, entry_id: int) -> JournalEntry | None:
        """Fetch one row by id."""
        async with self._sessionmaker() as session:
            return await session.get(JournalEntry, entry_id)

    async def by_day(
        self, calendar_id: str, plan_date: date_type
    ) -> list[JournalEntry]:
        """All rows for one calendar-day, oldest first."""
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(JournalEntry)
                .where(JournalEntry.calendar_id == calendar_id)
                .where(JournalEntry.plan_date == plan_date)
                .order_by(JournalEntry.id)
            )
            return list(result.scalars().all())

    async def by_tx_id(self, tx_id: str) -> JournalEntry | None:
        """Fetch a commit row by its transaction id.

        Undo needs this: the pre-commit calendar state and the post-commit
        etags live on the row, so undo works after a restart.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(JournalEntry).where(JournalEntry.tx_id == tx_id)
            )
            return result.scalars().first()


__all__ = [
    "DEFAULT_JOURNAL_PATH",
    "JournalStore",
    "init_journal",
    "init_journal_cli",
    "journal_sessionmaker",
]
