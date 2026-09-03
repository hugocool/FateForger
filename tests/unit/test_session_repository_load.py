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
