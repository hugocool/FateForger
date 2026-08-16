# tests/unit/tmbx/test_service.py
"""Plan service — read, apply, commit, undo.

Adapted from the task-14 brief's reference test to the real signatures that
landed after the brief was written:

* ``FakeCalendar.list_day``/``mutate`` are calendar_id-scoped (Task 13 fix,
  commit a02f866) — the brief's ``calendar.mutate("e1")`` and
  ``calendar.list_day("primary", DAY)`` predate that and would raise
  TypeError/KeyError against the real fake.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent
from tmbx.core.ops import Patch, UpdateBlock
from tmbx.journal.store import JournalStore, init_journal
from tmbx.service import ConflictError, PlanService

DAY = date(2026, 8, 17)
TZ = "Europe/Amsterdam"


def _event(eid, h, start_h, end_h, uid=None):
    return CalendarEvent(
        event_id=eid,
        summary=f"Block {h}",
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        uid=uid or f"u-{eid}",
        handle=h,
    )


@pytest.fixture
async def service(tmp_path):
    calendar = FakeCalendar({"primary": [_event("e1", "PR1", 9, 10), _event("e2", "DW1", 10, 12)]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    svc = PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")
    return svc


async def test_read_returns_plan_and_snapshot(service):
    plan, snapshot = await service.read("primary", DAY)
    assert [b.h for b in plan.blocks] == ["PR1", "DW1"]
    assert snapshot.token


async def test_apply_is_pure_and_writes_nothing_to_the_calendar(service):
    _, snapshot = await service.read("primary", DAY)
    result = await service.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert result.plan.by_handle("DW1").n == "Renamed"
    live = await service.calendar.list_day("primary", DAY, TZ)
    assert all(e.summary != "Renamed" for e in live)


async def test_apply_journals_an_attempt(service):
    _, snapshot = await service.read("primary", DAY)
    await service.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    rows = await service.store.by_day("primary", DAY)
    assert len(rows) == 1


async def test_commit_writes_to_the_calendar_and_returns_a_tx_id(service):
    _, snapshot = await service.read("primary", DAY)
    result = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert result.committed and result.tx_id
    live = await service.calendar.list_day("primary", DAY, TZ)
    assert any(e.summary == "Renamed" for e in live)


async def test_commit_refuses_when_the_calendar_drifted(service):
    _, snapshot = await service.read("primary", DAY)
    service.calendar.mutate("primary", "e1")
    with pytest.raises(ConflictError) as excinfo:
        await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert "e1" in excinfo.value.conflicts


async def test_commit_forces_when_asked(service):
    _, snapshot = await service.read("primary", DAY)
    service.calendar.mutate("primary", "e1")
    result = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]), expect="force"
    )
    assert result.committed


async def test_undo_restores_and_is_itself_journaled(service):
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    await service.undo(committed.tx_id)

    live = await service.calendar.list_day("primary", DAY, TZ)
    assert any(e.summary == "Block DW1" for e in live)

    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].undoes_tx == committed.tx_id


async def test_undo_refuses_to_clobber_a_newer_edit(service):
    """The failure the legacy undo has: restoring over an edit made since."""
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    service.calendar.mutate("primary", "e2")
    with pytest.raises(ConflictError):
        await service.undo(committed.tx_id)


async def test_undo_survives_a_restart(service):
    """Undo state lives in the journal, not process memory — #112.

    A fresh service sharing only the calendar and the store must still undo.
    """
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))

    restarted = PlanService(service.calendar, service.store, mint_uid=lambda: "u-x")
    await restarted.undo(committed.tx_id)

    live = await service.calendar.list_day("primary", DAY, TZ)
    assert any(e.summary == "Block DW1" for e in live)
