from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintBase,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
    ConstraintStore,
    ensure_constraint_schema,
)


@pytest.mark.asyncio
async def test_shared_scope_add_is_upserted_across_threads(tmp_path) -> None:
    db_path = tmp_path / "constraints.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    await ensure_constraint_schema(engine)
    store = ConstraintStore(sessionmaker)

    payload = ConstraintBase(
        name="Morning routine",
        description="Default morning routine",
        necessity=ConstraintNecessity.SHOULD,
        status=ConstraintStatus.PROPOSED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        tags=["routine"],
        hints={"rule_kind": "sequencing"},
    )
    await store.add_constraints(
        user_id="U1",
        channel_id="C1",
        thread_ts="t1",
        constraints=[payload],
    )
    await store.add_constraints(
        user_id="U1",
        channel_id="C2",
        thread_ts="t2",
        constraints=[payload],
    )

    rows = await store.list_constraints(user_id="U1")
    assert len(rows) == 1
    assert rows[0].scope == ConstraintScope.PROFILE
    assert rows[0].thread_ts is None
    assert rows[0].channel_id is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_shared_scope_canonical_precedence_prefers_locked(tmp_path) -> None:
    db_path = tmp_path / "constraints.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    await ensure_constraint_schema(engine)
    store = ConstraintStore(sessionmaker)

    proposed = ConstraintBase(
        name="Office commute",
        description="Commute to office",
        necessity=ConstraintNecessity.MUST,
        status=ConstraintStatus.PROPOSED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        tags=["commute"],
        hints={"rule_kind": "sequencing"},
    )
    locked = ConstraintBase(
        name="Office commute",
        description="Commute to office in the morning",
        necessity=ConstraintNecessity.MUST,
        status=ConstraintStatus.LOCKED,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        tags=["commute"],
        hints={"rule_kind": "sequencing"},
    )
    await store.add_constraints(
        user_id="U1", channel_id="C1", thread_ts="t1", constraints=[proposed]
    )
    await store.add_constraints(
        user_id="U1", channel_id="C1", thread_ts="t2", constraints=[locked]
    )

    rows = await store.list_constraints(user_id="U1")
    assert len(rows) == 1
    assert rows[0].status == ConstraintStatus.LOCKED
    assert rows[0].description == "Commute to office in the morning"

    await engine.dispose()


@pytest.mark.asyncio
async def test_prune_shared_constraints_dry_run_reports_without_mutation(tmp_path) -> None:
    db_path = tmp_path / "constraints.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    await ensure_constraint_schema(engine)
    store = ConstraintStore(sessionmaker)

    async with sessionmaker() as session:
        first = Constraint(
            user_id="U1",
            channel_id="C1",
            thread_ts="t1",
            name="Lunch break",
            description="Lunch at 13:00",
            necessity=ConstraintNecessity.SHOULD,
            status=ConstraintStatus.LOCKED,
            source=ConstraintSource.USER,
            scope=ConstraintScope.PROFILE,
            tags=["lunch"],
            hints={"rule_kind": "buffer"},
            updated_at=datetime(2026, 3, 6, 10, 0, tzinfo=timezone.utc),
        )
        second = Constraint(
            user_id="U1",
            channel_id="C2",
            thread_ts="t2",
            name="Lunch break",
            description="Lunch at 13:00 duplicate",
            necessity=ConstraintNecessity.SHOULD,
            status=ConstraintStatus.PROPOSED,
            source=ConstraintSource.USER,
            scope=ConstraintScope.PROFILE,
            tags=["lunch"],
            hints={"rule_kind": "buffer"},
            updated_at=datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc),
        )
        session.add_all([first, second])
        await session.commit()

    result = await store.prune_shared_constraints(user_id="U1", dry_run=True)
    assert result["duplicate_groups"] == 1
    assert result["duplicates_found"] == 1
    assert result["duplicates_archived"] == 0

    rows = await store.list_constraints(user_id="U1")
    assert len(rows) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_prune_shared_constraints_apply_archives_duplicates(tmp_path) -> None:
    db_path = tmp_path / "constraints.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    await ensure_constraint_schema(engine)
    store = ConstraintStore(sessionmaker)

    async with sessionmaker() as session:
        first = Constraint(
            user_id="U1",
            channel_id="C1",
            thread_ts="t1",
            name="Gym",
            description="Gym in evening",
            necessity=ConstraintNecessity.SHOULD,
            status=ConstraintStatus.LOCKED,
            source=ConstraintSource.USER,
            scope=ConstraintScope.PROFILE,
            tags=["sports"],
            hints={"rule_kind": "capacity"},
            updated_at=datetime(2026, 3, 6, 10, 0, tzinfo=timezone.utc),
        )
        second = Constraint(
            user_id="U1",
            channel_id="C2",
            thread_ts="t2",
            name="Gym",
            description="Gym in evening duplicate",
            necessity=ConstraintNecessity.SHOULD,
            status=ConstraintStatus.PROPOSED,
            source=ConstraintSource.USER,
            scope=ConstraintScope.PROFILE,
            tags=["sports"],
            hints={"rule_kind": "capacity"},
            updated_at=datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc),
        )
        session.add_all([first, second])
        await session.commit()

    result = await store.prune_shared_constraints(user_id="U1", dry_run=False)
    assert result["duplicate_groups"] == 1
    assert result["duplicates_found"] == 1
    assert result["duplicates_archived"] == 1

    rows = await store.list_constraints(user_id="U1", include_shared_scopes=True)
    assert len(rows) == 1
    assert rows[0].status == ConstraintStatus.LOCKED

    stats = await store.shared_scope_stats(user_id="U1")
    assert stats["raw_shared_rows"] == 2
    assert stats["canonical_shared_rows"] == 1

    await engine.dispose()
