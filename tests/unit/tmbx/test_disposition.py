# tests/unit/tmbx/test_disposition.py
from __future__ import annotations

from datetime import date

from tmbx.journal.disposition import Disposition, derive_dispositions
from tmbx.journal.models import EntryKind, JournalEntry, PatchOutcome

DAY = date(2026, 8, 17)


def _entry(eid, kind, *, tx_id=None, undoes_tx=None, outcome=PatchOutcome.APPLIED):
    return JournalEntry(
        id=eid,
        kind=kind,
        calendar_id="primary",
        plan_date=DAY,
        ops_json="{}",
        ops_schema_version=1,
        outcome=outcome,
        tx_id=tx_id,
        undoes_tx=undoes_tx,
    )


def test_lone_commit_is_accepted():
    entries = [_entry(1, EntryKind.COMMIT, tx_id="tx1")]
    assert derive_dispositions(entries) == {1: Disposition.ACCEPTED}


def test_commit_followed_by_undo_is_undone():
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.UNDO, tx_id="tx2", undoes_tx="tx1"),
    ]
    result = derive_dispositions(entries)
    assert result[1] == Disposition.UNDONE
    assert result[2] == Disposition.ACCEPTED


def test_earlier_commit_is_superseded():
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.COMMIT, tx_id="tx2"),
    ]
    result = derive_dispositions(entries)
    assert result[1] == Disposition.SUPERSEDED
    assert result[2] == Disposition.ACCEPTED


def test_undone_beats_superseded():
    """An undone commit stays undone even if a later commit exists."""
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.UNDO, tx_id="tx2", undoes_tx="tx1"),
        _entry(3, EntryKind.COMMIT, tx_id="tx3"),
    ]
    assert derive_dispositions(entries)[1] == Disposition.UNDONE


def test_applied_attempt_never_committed_is_abandoned():
    entries = [_entry(1, EntryKind.ATTEMPT)]
    assert derive_dispositions(entries) == {1: Disposition.ABANDONED}


def test_failed_attempt_is_failed_not_abandoned():
    entries = [_entry(1, EntryKind.ATTEMPT, outcome=PatchOutcome.APPLY_FAILED)]
    assert derive_dispositions(entries) == {1: Disposition.FAILED}
