"""The watcher's sleep boundary comes from the day's session, not from a model.

The session already holds the user's frame as a `DAY_FRAME` fact (typed, or
from memory at skeleton time). Reading it back for a day is one row's JSON,
no judgement.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from fateforger.agents.timeboxing.adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
)
from fateforger.agents.timeboxing.session_contracts import (
    Cancelled,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)


def _snapshot(key: str, day: date, *, sleep: str | None, status: str = "committed",
              day_type: DayType | None = None) -> PlanningSessionSnapshot:
    facts = []
    if sleep is not None:
        facts.append(PlanningFact(fact_id=f"frame-{key}", kind=FactKind.DAY_FRAME,
                                  value={"wake": "07:00", "sleep": sleep}, source="user"))
    return PlanningSessionSnapshot(
        session_key=key, revision=3, owner_user_id="U1", status=status,
        planning_day=PlanningDay.lock_default(value=day, timezone="Europe/Amsterdam",
                                              lock_revision=1, day_type=day_type),
        facts=facts,
    )


@pytest.mark.asyncio
async def test_the_in_memory_repository_returns_the_days_frame_or_none():
    repo = InMemoryPlanningSessionRepository([
        _snapshot("C1:1.0", date(2026, 9, 7), sleep="23:00"),
        _snapshot("C1:2.0", date(2026, 9, 8), sleep=None),
    ])
    assert (await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 7)))["sleep"] == "23:00"
    assert await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 8)) is None
    assert await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 9)) is None
    assert await repo.day_frame_for(owner_user_id="U2", planning_date=date(2026, 9, 7)) is None


@pytest.mark.asyncio
async def test_the_sql_repository_reads_one_rows_frame(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from fateforger.slack_bot.timeboxing_session_store import (
        SqlAlchemyTimeboxingSessionRepository,
    )

    # No `ensure_timeboxing_session_schema` helper exists on the module; the
    # repository's table is stood up the same way the rest of the suite does
    # it, via the real alembic migration (see test_timeboxing_session_store.py).
    database_path = tmp_path / "a.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    repo = SqlAlchemyTimeboxingSessionRepository(async_sessionmaker(engine, expire_on_commit=False))
    day = date(2026, 9, 7)
    snapshot = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    await repo.save(
        _snapshot("C1:1.0", day, sleep="22:30"),
        interaction_id="i1",
        outcome=Cancelled(),
        expected_revision=snapshot.revision,
    )
    frame = await repo.day_frame_for(owner_user_id="U1", planning_date=day)
    assert frame is not None and frame["sleep"] == "22:30"
    assert await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 8)) is None


@pytest.mark.asyncio
async def test_the_in_memory_repository_returns_the_days_locked_day_type():
    """R6: the session's locked day is what the user and the host agreed the
    day is. Weekday arithmetic calls a Tuesday of annual leave a working day,
    and the watcher then asks memory for the wrong day's rules."""
    repo = InMemoryPlanningSessionRepository([
        _snapshot("C1:1.0", date(2026, 9, 8), sleep="23:00", day_type=DayType.VACATION),
        _snapshot("C1:2.0", date(2026, 9, 9), sleep="23:00"),
    ])
    assert await repo.day_type_for(owner_user_id="U1", planning_date=date(2026, 9, 8)) == "vacation"
    assert await repo.day_type_for(owner_user_id="U1", planning_date=date(2026, 9, 9)) == "working"
    assert await repo.day_type_for(owner_user_id="U1", planning_date=date(2026, 9, 10)) is None
    assert await repo.day_type_for(owner_user_id="U2", planning_date=date(2026, 9, 8)) is None


@pytest.mark.asyncio
async def test_the_sql_repository_reads_one_rows_day_type(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from fateforger.slack_bot.timeboxing_session_store import (
        SqlAlchemyTimeboxingSessionRepository,
    )

    database_path = tmp_path / "b.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    repo = SqlAlchemyTimeboxingSessionRepository(async_sessionmaker(engine, expire_on_commit=False))
    day = date(2026, 9, 8)
    snapshot = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    await repo.save(
        _snapshot("C1:1.0", day, sleep="22:30", day_type=DayType.VACATION),
        interaction_id="i1",
        outcome=Cancelled(),
        expected_revision=snapshot.revision,
    )
    assert await repo.day_type_for(owner_user_id="U1", planning_date=day) == "vacation"
    assert await repo.day_type_for(owner_user_id="U1", planning_date=date(2026, 9, 9)) is None
