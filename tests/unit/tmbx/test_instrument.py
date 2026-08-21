# tests/unit/tmbx/test_instrument.py
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from tmbx.journal.instrument import (
    UNRESOLVED_CALENDAR_ID,
    JournalingPatcher,
    JournalingSubmitter,
)
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

    async def apply_patch_legacy(self, **kwargs):
        if self.raises:
            raise self.raises
        # Legacy interface returns a Timebox-like object directly, not a
        # (plan, patch) tuple.
        return SimpleNamespace(date=DAY)


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


async def test_default_calendar_id_fn_records_unresolved_not_primary(store):
    """With no resolver supplied, the row must not claim "primary" — that is
    an invented value, not a real fact about which calendar the session
    concerns. It must be recorded as unresolved instead."""
    patcher = JournalingPatcher(_FakePatcher(), store)
    await patcher.apply_patch(
        stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
    )

    assert await store.by_day("primary", DAY) == []

    rows = await store.by_day(UNRESOLVED_CALENDAR_ID, DAY)
    assert len(rows) == 1
    assert rows[0].calendar_id == UNRESOLVED_CALENDAR_ID


async def test_calendar_id_fn_raising_records_unresolved_not_primary(store):
    """A supplied resolver that raises is still an absence of a real answer
    — it must not fall back to "primary" either."""

    def _boom() -> str:
        raise RuntimeError("no session bound")

    patcher = JournalingPatcher(_FakePatcher(), store, calendar_id_fn=_boom)
    await patcher.apply_patch(
        stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
    )

    assert await store.by_day("primary", DAY) == []

    rows = await store.by_day(UNRESOLVED_CALENDAR_ID, DAY)
    assert len(rows) == 1


async def test_submit_with_no_calendar_id_records_unresolved_not_primary(store):
    """No caller in agent.py passes calendar_id to submit_plan today, so the
    kwargs.get(..., "primary") default silently attributed every commit row
    to "primary" — the same defect as the patcher's calendar_id_fn."""
    sub = JournalingSubmitter(_FakeSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY))

    assert await store.by_day("primary", DAY) == []

    rows = await store.by_day(UNRESOLVED_CALENDAR_ID, DAY)
    assert len(rows) == 1
    assert rows[0].kind is EntryKind.COMMIT
    assert rows[0].calendar_id == UNRESOLVED_CALENDAR_ID
    assert getattr(tx, "tmbx_calendar_id", None) == UNRESOLVED_CALENDAR_ID


async def test_submit_with_explicit_calendar_id_is_unchanged(store):
    """When a caller does supply a calendar id, behaviour is unchanged."""
    sub = JournalingSubmitter(_FakeSubmitter(), store)
    await sub.submit_plan(SimpleNamespace(date=DAY), calendar_id="cal-explicit")

    assert await store.by_day(UNRESOLVED_CALENDAR_ID, DAY) == []

    rows = await store.by_day("cal-explicit", DAY)
    assert len(rows) == 1


async def test_undo_of_unresolved_calendar_commit_stays_unresolved(store):
    """undo_transaction reads tmbx_calendar_id off the stamped transaction.
    If the commit itself was unresolved, the undo row must stay unresolved
    too rather than falling back to "primary"."""
    sub = JournalingSubmitter(_FakeSubmitter(), store)
    tx = await sub.submit_plan(SimpleNamespace(date=DAY))
    await sub.undo_transaction(tx)

    assert await store.by_day("primary", DAY) == []

    rows = await store.by_day(UNRESOLVED_CALENDAR_ID, DAY)
    assert len(rows) == 2
    assert rows[1].kind is EntryKind.UNDO
    assert rows[1].calendar_id == UNRESOLVED_CALENDAR_ID


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


# ── Legacy path: apply_patch_legacy must be journaled explicitly ──────────
# TimeboxPatcher.apply_patch_legacy calls self.apply_patch(...) on the
# *inner* (unwrapped) patcher, so relying on __getattr__ passthrough alone
# would leave this path unjournaled even though it resolves and works.


async def test_legacy_patch_writes_attempt_row(store):
    patcher = JournalingPatcher(_FakePatcher(), store, calendar_id_fn=lambda: "primary")
    result = await patcher.apply_patch_legacy(
        stage="Refine",
        current=SimpleNamespace(date=DAY),
        user_message="move lunch",
        constraints=[SimpleNamespace(hints={"uid": "c1", "extraction_reason": "graphflow_turn"})],
    )
    assert result is not None

    rows = await store.by_day("primary", DAY)
    assert len(rows) == 1
    assert rows[0].kind is EntryKind.ATTEMPT
    assert rows[0].outcome is PatchOutcome.APPLIED
    assert rows[0].instruction == "move lunch"
    assert rows[0].ops_json == "{}"
    assert rows[0].get_constraints()[0].uid == "c1"


async def test_legacy_patch_failure_writes_failure_row_and_reraises(store):
    patcher = JournalingPatcher(
        _FakePatcher(raises=ValueError("bad legacy patch")),
        store,
        calendar_id_fn=lambda: "primary",
    )
    with pytest.raises(ValueError):
        await patcher.apply_patch_legacy(
            stage="Refine", current=SimpleNamespace(date=DAY), user_message="x", constraints=[]
        )

    rows = await store.by_day("primary", DAY)
    assert rows[0].kind is EntryKind.ATTEMPT
    assert rows[0].outcome is PatchOutcome.APPLY_FAILED
    assert "bad legacy patch" in (rows[0].error or "")


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
