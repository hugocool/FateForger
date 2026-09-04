"""Behavior tests for restart-safe adaptive timeboxing session persistence."""

from __future__ import annotations

import asyncio
import shutil
from datetime import date
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.util import CommandError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from fateforger.agents.timeboxing.adaptive_timeboxing import StaleSessionRevision
from fateforger.agents.timeboxing.session_contracts import (
    AwaitingUser,
    Cancelled,
    DayType,
    HandledInteraction,
    PlanningDay,
)
from fateforger.slack_bot.timeboxing_session_store import (
    SqlAlchemyTimeboxingSessionRepository,
)


def _locked_day() -> PlanningDay:
    """Return a hand-checked planning-day fixture."""

    return PlanningDay(
        date=date(2026, 8, 31),
        timezone="Europe/Amsterdam",
        iso_weekday=1,
        day_type=DayType.WORKING,
        classification_basis="calendar",
        lock_revision=1,
    )


def _migrated_test_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Upgrade a disposable SQLite database and return its async accessors."""

    database_path = tmp_path / "adaptive-sessions.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_alembic_rejects_autogenerate_for_manual_migration_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Autogenerate must not infer destructive changes from partial metadata."""

    repository_root = Path(__file__).parents[2]
    script_location = tmp_path / "alembic"
    shutil.copytree(repository_root / "alembic", script_location)
    database_path = tmp_path / "manual-migrations.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(script_location))
    command.upgrade(config, "head")

    with pytest.raises(CommandError, match="does not provide a MetaData"):
        command.revision(
            config,
            message="autogenerate-must-remain-disabled",
            autogenerate=True,
        )


async def test_saved_snapshot_and_outcome_rehydrate_in_a_new_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart must not lose either state or the complete replay outcome."""

    engine, maker = _migrated_test_database(tmp_path, monkeypatch)
    try:
        first = SqlAlchemyTimeboxingSessionRepository(maker)
        current = await first.load_or_create("C1:1.0", owner_user_id="U1")
        outcome = AwaitingUser(
            requirement_id="planning_day",
            question="Which day should we plan?",
            why_needed="The calendar window depends on the selected day.",
        )

        saved = await first.save(
            current.model_copy(update={"planning_day": _locked_day()}),
            expected_revision=0,
            interaction_id="A1",
            outcome=outcome,
        )

        second = SqlAlchemyTimeboxingSessionRepository(maker)
        restored = await second.load_or_create("C1:1.0", owner_user_id="U1")
        restored_outcome = await second.load_outcome("C1:1.0", interaction_id="A1")

        assert saved.revision == 1
        assert restored.revision == 1
        assert restored.planning_day == PlanningDay(
            date=date(2026, 8, 31),
            timezone="Europe/Amsterdam",
            iso_weekday=1,
            day_type=DayType.WORKING,
            classification_basis="calendar",
            lock_revision=1,
        )
        assert restored.handled_interactions == [
            HandledInteraction(
                interaction_id="A1",
                outcome_kind="awaiting_user",
                session_revision=1,
            )
        ]
        assert restored_outcome == AwaitingUser(
            requirement_id="planning_day",
            question="Which day should we plan?",
            why_needed="The calendar window depends on the selected day.",
        )
    finally:
        await engine.dispose()


async def test_concurrent_saves_at_one_revision_have_exactly_one_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the SQL revision predicate must make this test fail."""

    engine, maker = _migrated_test_database(tmp_path, monkeypatch)
    try:
        repository = SqlAlchemyTimeboxingSessionRepository(maker)
        current = await repository.load_or_create("C1:2.0", owner_user_id="U1")

        results = await asyncio.gather(
            repository.save(
                current.model_copy(update={"planning_day": _locked_day()}),
                expected_revision=0,
                interaction_id="A1",
                outcome=AwaitingUser(
                    requirement_id="planning_day",
                    question="Which day should we plan?",
                    why_needed="A planning day is required.",
                ),
            ),
            repository.save(
                current.model_copy(update={"status": "cancelled"}),
                expected_revision=0,
                interaction_id="A2",
                outcome=Cancelled(),
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, BaseException) for result in results) == 1
        assert sum(isinstance(result, StaleSessionRevision) for result in results) == 1

        restored = await repository.load_or_create("C1:2.0", owner_user_id="U1")
        stored_outcomes = [
            await repository.load_outcome("C1:2.0", interaction_id="A1"),
            await repository.load_outcome("C1:2.0", interaction_id="A2"),
        ]
        assert restored.revision == 1
        assert sum(outcome is not None for outcome in stored_outcomes) == 1
    finally:
        await engine.dispose()


async def test_duplicate_interaction_id_replays_without_overwriting_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeated interaction must not advance state or replace its first outcome."""

    engine, maker = _migrated_test_database(tmp_path, monkeypatch)
    try:
        first = SqlAlchemyTimeboxingSessionRepository(maker)
        current = await first.load_or_create("C1:3.0", owner_user_id="U1")
        first_outcome = AwaitingUser(
            requirement_id="planning_day",
            question="Which day should we plan?",
            why_needed="A planning day is required.",
        )
        saved = await first.save(
            current.model_copy(update={"planning_day": _locked_day()}),
            expected_revision=0,
            interaction_id="A1",
            outcome=first_outcome,
        )

        second = SqlAlchemyTimeboxingSessionRepository(maker)
        replayed = await second.save(
            saved.model_copy(update={"status": "cancelled"}),
            expected_revision=1,
            interaction_id="A1",
            outcome=Cancelled(),
        )

        assert replayed == saved
        assert await second.load_outcome("C1:3.0", interaction_id="A1") == AwaitingUser(
            requirement_id="planning_day",
            question="Which day should we plan?",
            why_needed="A planning day is required.",
        )
    finally:
        await engine.dispose()


async def test_standing_reads_open_and_committed_sessions_from_the_indexed_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#256: the nudge suppressor asks this table, and only this table.

    Three sessions for one user: an open one saved just now, a day committed
    inside the window (the status column changes on save, not on create, so
    the committed one is saved through), and a cancelled one that must count
    as neither. A fourth belongs to someone else.
    """

    from datetime import datetime, timedelta, timezone

    engine, maker = _migrated_test_database(tmp_path, monkeypatch)
    try:
        repo = SqlAlchemyTimeboxingSessionRepository(maker)
        opened = await repo.load_or_create("C1:open", owner_user_id="U1")
        await repo.save(
            opened.model_copy(update={"planning_day": _locked_day()}),
            expected_revision=0,
            interaction_id="A1",
            outcome=AwaitingUser(
                requirement_id="x", question="q", why_needed="w"
            ),
        )
        committed = await repo.load_or_create("C1:done", owner_user_id="U1")
        await repo.save(
            committed.model_copy(
                update={"planning_day": _locked_day(), "status": "committed"}
            ),
            expected_revision=0,
            interaction_id="B1",
            outcome=Cancelled(),
        )
        cancelled = await repo.load_or_create("C1:gone", owner_user_id="U1")
        await repo.save(
            cancelled.model_copy(update={"status": "cancelled"}),
            expected_revision=0,
            interaction_id="C1",
            outcome=Cancelled(),
        )
        await repo.load_or_create("C2:open", owner_user_id="U2")

        now = datetime.now(timezone.utc)
        standing = await repo.standing_for(
            owner_user_id="U1",
            open_since=now - timedelta(hours=1),
            planned_from=date(2026, 8, 31),
            planned_to=date(2026, 9, 1),
        )
        assert standing.open_session_key == "C1:open"
        assert standing.committed_session_key == "C1:done"

        stale = await repo.standing_for(
            owner_user_id="U1",
            open_since=now + timedelta(minutes=1),
            planned_from=date(2026, 9, 1),
            planned_to=date(2026, 9, 2),
        )
        assert stale.open_session_key is None
        assert stale.committed_session_key is None
        assert not stale.under_way and not stale.planned
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_open_sessions_names_every_open_session_and_how_far_it_got(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#164, #299: which sessions this user holds open, and what each stands for.

    Five sessions for one user: one still at the opening turn's revision, one
    the user has worked in, one already cancelled, one planned for a different
    day, and one that never locked a day at all. A sixth belongs to someone
    else. Every open row of this user's comes back, each carrying the revision
    that says whether anybody touched it and the day it stands for -- None for
    the one that locked none, which is the row a day filter in SQL could never
    return (#299).
    """

    engine, maker = _migrated_test_database(tmp_path, monkeypatch)
    try:
        repo = SqlAlchemyTimeboxingSessionRepository(maker)

        untouched = await repo.load_or_create("C1:auto", owner_user_id="U1")
        await repo.save(
            untouched.model_copy(update={"planning_day": _locked_day()}),
            expected_revision=0,
            interaction_id="A1",
            outcome=AwaitingUser(requirement_id="x", question="q", why_needed="w"),
        )

        worked = await repo.load_or_create("C1:live", owner_user_id="U1")
        saved = await repo.save(
            worked.model_copy(update={"planning_day": _locked_day()}),
            expected_revision=0,
            interaction_id="B1",
            outcome=AwaitingUser(requirement_id="x", question="q", why_needed="w"),
        )
        await repo.save(
            saved,
            expected_revision=1,
            interaction_id="B2",
            outcome=AwaitingUser(requirement_id="y", question="q", why_needed="w"),
        )

        gone = await repo.load_or_create("C1:gone", owner_user_id="U1")
        await repo.save(
            gone.model_copy(
                update={"planning_day": _locked_day(), "status": "cancelled"}
            ),
            expected_revision=0,
            interaction_id="C1",
            outcome=Cancelled(),
        )

        other_day = await repo.load_or_create("C1:tomorrow", owner_user_id="U1")
        await repo.save(
            other_day.model_copy(
                update={
                    "planning_day": _locked_day().model_copy(
                        update={"date": date(2026, 9, 1), "iso_weekday": 2}
                    )
                }
            ),
            expected_revision=0,
            interaction_id="D1",
            outcome=AwaitingUser(requirement_id="x", question="q", why_needed="w"),
        )

        await repo.load_or_create("C1:dayless", owner_user_id="U1")

        stranger = await repo.load_or_create("C2:auto", owner_user_id="U2")
        await repo.save(
            stranger.model_copy(update={"planning_day": _locked_day()}),
            expected_revision=0,
            interaction_id="E1",
            outcome=AwaitingUser(requirement_id="x", question="q", why_needed="w"),
        )

        rows = await repo.open_sessions(owner_user_id="U1")

        assert {row.session_key: row.revision for row in rows} == {
            "C1:auto": 1,
            "C1:live": 2,
            "C1:tomorrow": 1,
            "C1:dayless": 0,
        }
        assert {row.session_key: row.planning_date for row in rows} == {
            "C1:auto": date(2026, 8, 31),
            "C1:live": date(2026, 8, 31),
            "C1:tomorrow": date(2026, 9, 1),
            "C1:dayless": None,
        }
        # Newest save first, so a caller reading one row reads the freshest.
        assert [row.updated_at for row in rows] == sorted(
            (row.updated_at for row in rows), reverse=True
        )

        assert await repo.open_sessions(owner_user_id="U3") == []
    finally:
        await engine.dispose()
