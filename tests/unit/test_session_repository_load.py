from __future__ import annotations

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
)


@pytest.mark.asyncio
async def test_load_returns_none_for_unknown_session_and_creates_nothing() -> None:
    repo = InMemoryPlanningSessionRepository([])

    assert await repo.load("D1:dm") is None
    # A later load_or_create must still start at revision 0: load() wrote nothing.
    created = await repo.load_or_create("D1:dm", owner_user_id="U1")
    assert created.revision == 0


@pytest.mark.asyncio
async def test_load_returns_a_copy_of_an_existing_session() -> None:
    repo = InMemoryPlanningSessionRepository([])
    created = await repo.load_or_create("C1:1.0", owner_user_id="U1")

    loaded = await repo.load("C1:1.0")

    assert loaded is not None
    assert loaded == created
    assert loaded is not created


@pytest.mark.asyncio
async def test_open_sessions_for_day_answers_with_the_revision_that_tells_them_apart() -> None:
    """The in-memory ledger answers expiry's question the same way SQL does."""

    from datetime import date

    from fateforger.agents.timeboxing.session_contracts import (
        AwaitingUser,
        DayType,
        PlanningDay,
    )

    day = PlanningDay(
        date=date(2026, 9, 4),
        timezone="Europe/Amsterdam",
        iso_weekday=5,
        day_type=DayType.WORKING,
        classification_basis="calendar",
        lock_revision=1,
    )
    repo = InMemoryPlanningSessionRepository([])

    async def _turn(session_key: str, *, owner: str, expected: int, day_lock, status="open"):
        snapshot = await repo.load_or_create(session_key, owner_user_id=owner)
        return await repo.save(
            snapshot.model_copy(update={"planning_day": day_lock, "status": status}),
            expected_revision=expected,
            interaction_id=f"{session_key}:{expected}",
            outcome=AwaitingUser(requirement_id="x", question="q", why_needed="w"),
        )

    await _turn("C1:auto", owner="U1", expected=0, day_lock=day)
    await _turn("C1:live", owner="U1", expected=0, day_lock=day)
    await _turn("C1:live", owner="U1", expected=1, day_lock=day)
    await _turn("C1:gone", owner="U1", expected=0, day_lock=day, status="cancelled")
    await _turn("C2:auto", owner="U2", expected=0, day_lock=day)
    await repo.load_or_create("C1:dayless", owner_user_id="U1")

    rows = await repo.open_sessions_for_day(
        owner_user_id="U1", planning_date=date(2026, 9, 4)
    )

    assert {row.session_key: row.revision for row in rows} == {
        "C1:auto": 1,
        "C1:live": 2,
    }
    assert (
        await repo.open_sessions_for_day(
            owner_user_id="U1", planning_date=date(2026, 9, 5)
        )
        == []
    )
