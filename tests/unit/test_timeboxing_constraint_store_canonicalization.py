from __future__ import annotations

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


def _shared_constraint(*, status: ConstraintStatus) -> ConstraintBase:
    return ConstraintBase(
        name="No calls after 17:00",
        description="Protect evening deep work.",
        necessity=ConstraintNecessity.SHOULD,
        status=status,
        source=ConstraintSource.USER,
        scope=ConstraintScope.PROFILE,
        hints={"uid": "uid:no_calls_after_17"},
    )


@pytest.mark.asyncio
async def test_upsert_constraints_dedupes_shared_scope_across_threads() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_constraint_schema(engine)
        store = ConstraintStore(async_sessionmaker(engine, expire_on_commit=False))

        first = await store.upsert_constraints(
            user_id="u1",
            channel_id="c1",
            thread_ts="t1",
            constraints=[_shared_constraint(status=ConstraintStatus.PROPOSED)],
        )
        second = await store.upsert_constraints(
            user_id="u1",
            channel_id="c1",
            thread_ts="t2",
            constraints=[_shared_constraint(status=ConstraintStatus.PROPOSED)],
        )

        rows = await store.list_constraints(
            user_id="u1",
            channel_id="c1",
            scope=ConstraintScope.PROFILE,
        )
        assert first["added"] == 1
        assert second["added"] == 0
        assert second["skipped"] == 1
        assert len(rows) == 1
        assert rows[0].thread_ts is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prune_shared_constraints_dry_run_reports_without_mutation() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_constraint_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        store = ConstraintStore(sessionmaker)

        # Insert 3 rows directly (simulating pre-deduplication dirty state)
        async with sessionmaker() as session:
            for status in [
                ConstraintStatus.PROPOSED,
                ConstraintStatus.LOCKED,
                ConstraintStatus.DECLINED,
            ]:
                session.add(
                    Constraint(
                        user_id="u1",
                        name="No calls after 17:00",
                        description="Protect evening deep work.",
                        necessity=ConstraintNecessity.SHOULD,
                        status=status,
                        source=ConstraintSource.USER,
                        scope=ConstraintScope.PROFILE,
                        hints={"uid": "uid:no_calls_after_17"},
                    )
                )
            await session.commit()

        before = await store.list_constraints(
            user_id="u1",
            scope=ConstraintScope.PROFILE,
        )
        preview = await store.prune_shared_constraints(
            user_id="u1",
            dry_run=True,
        )
        after = await store.list_constraints(
            user_id="u1",
            scope=ConstraintScope.PROFILE,
        )

        assert len(before) == 3
        assert len(after) == 3
        assert preview["raw_shared_rows"] == 3
        assert preview["canonical_shared_rows"] == 1
        assert preview["duplicates_found"] == 2
        assert preview["duplicates_archived"] == 0  # dry_run: no mutation
        assert preview["duplicate_groups"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prune_shared_constraints_apply_keeps_single_canonical_row() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_constraint_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        store = ConstraintStore(sessionmaker)

        # Insert 3 rows directly (simulating pre-deduplication dirty state)
        async with sessionmaker() as session:
            for status in [
                ConstraintStatus.PROPOSED,
                ConstraintStatus.LOCKED,
                ConstraintStatus.DECLINED,
            ]:
                session.add(
                    Constraint(
                        user_id="u1",
                        name="No calls after 17:00",
                        description="Protect evening deep work.",
                        necessity=ConstraintNecessity.SHOULD,
                        status=status,
                        source=ConstraintSource.USER,
                        scope=ConstraintScope.PROFILE,
                        hints={"uid": "uid:no_calls_after_17"},
                    )
                )
            await session.commit()

        apply_result = await store.prune_shared_constraints(
            user_id="u1",
            dry_run=False,
        )
        # Use include_shared_scopes to get the canonical view (archive approach keeps rows)
        remaining = await store.list_constraints(
            user_id="u1",
            scope=ConstraintScope.PROFILE,
            include_shared_scopes=True,
        )

        assert apply_result["duplicates_found"] == 2
        assert apply_result["duplicates_archived"] >= 1  # at least PROPOSED archived
        assert len(remaining) == 1
        assert remaining[0].status == ConstraintStatus.LOCKED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replace_session_constraints_replaces_thread_snapshot() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await ensure_constraint_schema(engine)
        store = ConstraintStore(async_sessionmaker(engine, expire_on_commit=False))
        await store.add_constraints(
            user_id="u1",
            channel_id="c1",
            thread_ts="t1",
            constraints=[
                ConstraintBase(
                    name="Old session rule",
                    description="legacy",
                    necessity=ConstraintNecessity.SHOULD,
                    status=ConstraintStatus.PROPOSED,
                    source=ConstraintSource.USER,
                    scope=ConstraintScope.SESSION,
                )
            ],
        )
        replaced = await store.replace_session_constraints(
            user_id="u1",
            channel_id="c1",
            thread_ts="t1",
            constraints=[
                ConstraintBase(
                    name="New session rule",
                    description="fresh",
                    necessity=ConstraintNecessity.MUST,
                    status=ConstraintStatus.LOCKED,
                    source=ConstraintSource.USER,
                    scope=ConstraintScope.SESSION,
                )
            ],
        )
        rows = await store.list_constraints(
            user_id="u1",
            channel_id="c1",
            thread_ts="t1",
            scope=ConstraintScope.SESSION,
        )

        assert len(replaced) == 1
        assert len(rows) == 1
        assert rows[0].name == "New session rule"
    finally:
        await engine.dispose()
