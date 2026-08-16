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
        # "undone" is the real success status undo_sync sets (sync_engine.py:524);
        # a successful commit's status is "committed", but that string is never
        # reused for a successful undo.
        return SimpleNamespace(status="undone", ops=[], results=[])


async def test_successful_patch_writes_attempt_row(store):
    patcher = JournalingPatcher(_FakePatcher(), store, calendar_id_fn=lambda: "primary")
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
        _FakePatcher(raises=ValueError("bad patch")), store, calendar_id_fn=lambda: "primary"
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

    patcher = JournalingPatcher(_FakePatcher(), _BrokenStore(), calendar_id_fn=lambda: "primary")
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
    assert rows[1].outcome is PatchOutcome.APPLIED


# ── Important 1: constraint_refs() must never break planning ──────────────


async def test_broken_constraint_refs_never_breaks_planning(store):
    """A constraint whose attribute access raises (e.g. a detached ORM
    instance) must not stop apply_patch from returning its result."""

    class _ExplodingConstraint:
        @property
        def hints(self):
            raise RuntimeError("DetachedInstanceError")

    patcher = JournalingPatcher(_FakePatcher(), store, calendar_id_fn=lambda: "primary")
    plan, patch = await patcher.apply_patch(
        stage="Refine",
        current=SimpleNamespace(date=DAY),
        user_message="x",
        constraints=[_ExplodingConstraint()],
    )
    assert plan is not None

    rows = await store.by_day("primary", DAY)
    assert len(rows) == 1
    assert rows[0].get_constraints() == []


# ── Important 2: partial commits/undos must not be journaled as APPLIED ───


async def test_partial_submit_is_not_journaled_as_applied(store):
    class _PartialSubmitter:
        async def submit_plan(self, desired, **kwargs):
            return SimpleNamespace(status="partial_halted", ops=[], results=[])

    sub = JournalingSubmitter(_PartialSubmitter(), store)
    await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")

    rows = await store.by_day("primary", DAY)
    assert rows[0].kind is EntryKind.COMMIT
    assert rows[0].outcome is not PatchOutcome.APPLIED
    assert rows[0].outcome is PatchOutcome.APPLY_FAILED


async def test_partial_undo_is_not_journaled_as_applied(store):
    class _PartialUndoSubmitter:
        async def submit_plan(self, desired, **kwargs):
            return SimpleNamespace(status="committed", ops=[], results=[])

        async def undo_transaction(self, tx):
            return SimpleNamespace(status="undo_partial", ops=[], results=[])

    sub = JournalingSubmitter(_PartialUndoSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")
    await sub.undo_transaction(tx)

    rows = await store.by_day("primary", DAY)
    assert rows[1].kind is EntryKind.UNDO
    assert rows[1].outcome is not PatchOutcome.APPLIED
    assert rows[1].outcome is PatchOutcome.APPLY_FAILED


# ── Important 3: calendar_id_fn is resolved per call, not fixed at init ───


async def test_calendar_id_fn_is_resolved_per_call(store):
    ids = iter(["cal-a", "cal-b"])
    patcher = JournalingPatcher(_FakePatcher(), store, calendar_id_fn=lambda: next(ids))
    await patcher.apply_patch(
        stage="Refine", current=SimpleNamespace(date=DAY), user_message="first", constraints=[]
    )
    await patcher.apply_patch(
        stage="Refine", current=SimpleNamespace(date=DAY), user_message="second", constraints=[]
    )

    rows_a = await store.by_day("cal-a", DAY)
    rows_b = await store.by_day("cal-b", DAY)
    assert len(rows_a) == 1 and rows_a[0].instruction == "first"
    assert len(rows_b) == 1 and rows_b[0].instruction == "second"


async def test_default_calendar_id_fn_is_primary(store):
    """Documented default: with no resolver supplied, attempts are attributed
    to "primary" — a known limitation until a caller wires a real resolver."""
    patcher = JournalingPatcher(_FakePatcher(), store)
    await patcher.apply_patch(
        stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
    )
    rows = await store.by_day("primary", DAY)
    assert len(rows) == 1


# ── Important 4: __getattr__ passthrough ───────────────────────────────────


async def test_patcher_getattr_passes_through_to_inner(store):
    inner = _FakePatcher()
    inner.some_marker = "sentinel"
    patcher = JournalingPatcher(inner, store, calendar_id_fn=lambda: "primary")
    assert patcher.some_marker == "sentinel"


async def test_submitter_getattr_passes_through_to_inner(store):
    inner = _FakeSubmitter()
    inner.some_marker = "sentinel"
    sub = JournalingSubmitter(inner, store)
    assert sub.some_marker == "sentinel"


async def test_submitter_last_transaction_passes_through(store):
    inner = _FakeSubmitter()
    sentinel_tx = SimpleNamespace(status="committed")
    inner.last_transaction = sentinel_tx
    sub = JournalingSubmitter(inner, store)
    assert sub.last_transaction is sentinel_tx


# ── Minor: submit_plan / undo_transaction journal inner failures too ──────


async def test_submit_failure_writes_failure_row_and_reraises(store):
    class _RaisingSubmitter:
        async def submit_plan(self, desired, **kwargs):
            raise RuntimeError("calendar api down")

    sub = JournalingSubmitter(_RaisingSubmitter(), store)
    with pytest.raises(RuntimeError):
        await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")

    rows = await store.by_day("primary", DAY)
    assert rows[0].kind is EntryKind.COMMIT
    assert rows[0].outcome is PatchOutcome.APPLY_FAILED
    assert "calendar api down" in (rows[0].error or "")


async def test_undo_failure_writes_failure_row_and_reraises(store):
    class _RaisingUndoSubmitter:
        async def submit_plan(self, desired, **kwargs):
            return SimpleNamespace(status="committed", ops=[], results=[])

        async def undo_transaction(self, tx):
            raise RuntimeError("undo api down")

    sub = JournalingSubmitter(_RaisingUndoSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="primary")
    with pytest.raises(RuntimeError):
        await sub.undo_transaction(tx)

    rows = await store.by_day("primary", DAY)
    assert rows[1].kind is EntryKind.UNDO
    assert rows[1].outcome is PatchOutcome.APPLY_FAILED
    assert "undo api down" in (rows[1].error or "")
    assert rows[1].undoes_tx == rows[0].tx_id
