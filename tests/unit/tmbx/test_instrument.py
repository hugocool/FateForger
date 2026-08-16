# tests/unit/tmbx/test_instrument.py
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from tmbx.journal.instrument import JournalingPatcher, JournalingSubmitter
from tmbx.journal.models import EntryKind, PatchOutcome
from tmbx.journal.store import JournalStore, init_journal

DAY = date(2026, 8, 17)


@pytest.fixture
async def store(tmp_path):
    return JournalStore(await init_journal(tmp_path / "j.db"))


class _FakePatcher:
    def __init__(self, *, raises: Exception | None = None):
        self.raises = raises

    async def apply_patch(self, **kwargs):
        if self.raises:
            raise self.raises
        plan = SimpleNamespace(date=DAY)
        patch = SimpleNamespace(model_dump_json=lambda: '{"ops":[{"op":"ue"}]}')
        return plan, patch


class _FakeSubmitter:
    def __init__(self):
        self.last_transaction = None

    async def submit_plan(self, desired, **kwargs):
        return SimpleNamespace(status="committed", ops=[], results=[])

    async def undo_transaction(self, tx):
        return SimpleNamespace(status="committed", ops=[], results=[])


async def test_successful_patch_writes_attempt_row(store):
    patcher = JournalingPatcher(_FakePatcher(), store, calendar_id="primary")
    await patcher.apply_patch(
        stage="Refine",
        current=SimpleNamespace(date=DAY),
        user_message="move lunch",
        constraints=[SimpleNamespace(hints={"uid": "c1", "extraction_reason": "graphflow_turn"})],
    )

    rows = await store.by_day("primary", DAY)
    assert len(rows) == 1
    assert rows[0].kind is EntryKind.ATTEMPT
    assert rows[0].outcome is PatchOutcome.APPLIED
    assert rows[0].instruction == "move lunch"
    assert rows[0].get_constraints()[0].uid == "c1"
    assert rows[0].get_constraints()[0].reason == "graphflow_turn"


async def test_failed_patch_writes_failure_row_and_reraises(store):
    patcher = JournalingPatcher(
        _FakePatcher(raises=ValueError("bad patch")), store, calendar_id="primary"
    )
    with pytest.raises(ValueError):
        await patcher.apply_patch(
            stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
        )

    rows = await store.by_day("primary", DAY)
    assert rows[0].outcome is PatchOutcome.APPLY_FAILED
    assert "bad patch" in (rows[0].error or "")


async def test_journal_failure_never_breaks_planning(store):
    class _BrokenStore:
        async def append(self, entry):
            raise RuntimeError("disk full")

    patcher = JournalingPatcher(_FakePatcher(), _BrokenStore(), calendar_id="primary")
    plan, patch = await patcher.apply_patch(
        stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
    )
    assert plan is not None


async def test_submit_writes_commit_row_with_tx_id(store):
    sub = JournalingSubmitter(_FakeSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")

    rows = await store.by_day("primary", DAY)
    assert rows[0].kind is EntryKind.COMMIT
    assert rows[0].tx_id is not None
    assert getattr(tx, "tmbx_tx_id", None) == rows[0].tx_id


async def test_undo_row_references_the_commit(store):
    sub = JournalingSubmitter(_FakeSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")
    await sub.undo_transaction(tx)

    rows = await store.by_day("primary", DAY)
    assert rows[1].kind is EntryKind.UNDO
    assert rows[1].undoes_tx == rows[0].tx_id
