# tests/unit/tmbx/test_journal_store.py
from __future__ import annotations

from datetime import date

import pytest

from tmbx.journal.models import ConstraintRef, JournalEntry, PatchOutcome
from tmbx.journal.store import JournalStore, init_journal


@pytest.fixture
async def store(tmp_path):
    sessionmaker = await init_journal(tmp_path / "j.db")
    return JournalStore(sessionmaker)


async def test_append_returns_id_and_roundtrips(store):
    entry = JournalEntry(
        calendar_id="primary",
        plan_date=date(2026, 8, 17),
        instruction="move lunch to 13:00",
        ops_json='{"ops":[]}',
        ops_schema_version=1,
        outcome=PatchOutcome.APPLIED,
    )
    entry.set_constraints([ConstraintRef(uid="c1", uid_kind="minted", reason="graphflow_turn")])

    entry_id = await store.append(entry)
    assert entry_id > 0

    loaded = await store.get(entry_id)
    assert loaded is not None
    assert loaded.instruction == "move lunch to 13:00"
    assert loaded.outcome == PatchOutcome.APPLIED
    refs = loaded.get_constraints()
    assert refs == [ConstraintRef(uid="c1", uid_kind="minted", reason="graphflow_turn")]


async def test_by_day_filters_and_orders(store):
    for day, instr in [
        (date(2026, 8, 17), "first"),
        (date(2026, 8, 18), "other day"),
        (date(2026, 8, 17), "second"),
    ]:
        await store.append(
            JournalEntry(
                calendar_id="primary",
                plan_date=day,
                instruction=instr,
                ops_json="{}",
                ops_schema_version=1,
                outcome=PatchOutcome.APPLIED,
            )
        )

    rows = await store.by_day("primary", date(2026, 8, 17))
    assert [r.instruction for r in rows] == ["first", "second"]


async def test_by_day_scopes_to_calendar(store):
    await store.append(
        JournalEntry(
            calendar_id="work",
            plan_date=date(2026, 8, 17),
            instruction="work cal",
            ops_json="{}",
            ops_schema_version=1,
            outcome=PatchOutcome.APPLIED,
        )
    )
    assert await store.by_day("primary", date(2026, 8, 17)) == []
