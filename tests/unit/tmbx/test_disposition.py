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


def test_undo_of_earlier_commit_does_not_supersede_later_commit():
    """Critical 1 fix: UNDO rows should not count as commits for supersession.

    Two commits and an undo of the earlier one. The later commit should be
    ACCEPTED, not SUPERSEDED by the undo.
    """
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.COMMIT, tx_id="tx2"),
        _entry(3, EntryKind.UNDO, tx_id="tx3", undoes_tx="tx1"),
    ]
    result = derive_dispositions(entries)
    assert result[2] == Disposition.ACCEPTED, "Undo of earlier commit should not supersede later commit"
    assert result[1] == Disposition.UNDONE


def test_failed_undo_does_not_mark_target_as_undone():
    """Critical 2 fix: A failed UNDO should not mark its target as UNDONE.

    A commit followed by an UNDO with APPLY_FAILED should leave the commit
    as ACCEPTED (its changes are still live) and mark the undo as FAILED.
    """
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.UNDO, tx_id="tx2", undoes_tx="tx1", outcome=PatchOutcome.APPLY_FAILED),
    ]
    result = derive_dispositions(entries)
    assert result[1] == Disposition.ACCEPTED, "Commit should be ACCEPTED when undo fails"
    assert result[2] == Disposition.FAILED, "Failed undo should be FAILED"


def test_empty_input_returns_empty_dict():
    """Edge case: empty entries list."""
    assert derive_dispositions([]) == {}


def test_attempt_with_tx_id_in_undone_set_respects_precedence():
    """Important 3 fix: ATTEMPT→ABANDONED should come after UNDONE check.

    An ATTEMPT row that carries a tx_id present in undone_tx should be
    marked as UNDONE (by precedence), not ABANDONED. This pins that the
    precedence evaluation order is: failed → undone → superseded → abandoned.
    """
    entries = [
        _entry(1, EntryKind.ATTEMPT, tx_id="tx1"),
        _entry(2, EntryKind.UNDO, tx_id="tx2", undoes_tx="tx1"),
    ]
    result = derive_dispositions(entries)
    assert result[1] == Disposition.UNDONE, "ATTEMPT with tx_id in undone_tx should be UNDONE by precedence"
    assert result[2] == Disposition.ACCEPTED


def test_attempt_followed_by_unrelated_commit_is_not_superseded():
    """Critical 3 fix: SUPERSEDED must be gated to COMMIT rows.

    An ATTEMPT that applied but was never committed, followed by an unrelated
    COMMIT the same day, should be ABANDONED, not SUPERSEDED. This is the most
    common real abandonment: explore, decide not to commit, commit something else.
    """
    entries = [
        _entry(1, EntryKind.ATTEMPT, tx_id="tx1"),
        _entry(2, EntryKind.COMMIT, tx_id="tx2"),
    ]
    result = derive_dispositions(entries)
    assert result[1] == Disposition.ABANDONED, "Uncommitted ATTEMPT should be ABANDONED, not SUPERSEDED"
    assert result[2] == Disposition.ACCEPTED


def test_undo_row_not_superseded_by_later_commit():
    """Critical 3 fix: SUPERSEDED must be gated to COMMIT rows.

    A sequence: COMMIT, UNDO of it, then a later COMMIT.
    The UNDO row should be ACCEPTED (it succeeded), not SUPERSEDED.
    Supersession is a relation between commits; UNDOs are never superseded.
    """
    entries = [
        _entry(1, EntryKind.COMMIT, tx_id="tx1"),
        _entry(2, EntryKind.UNDO, tx_id="tx2", undoes_tx="tx1"),
        _entry(3, EntryKind.COMMIT, tx_id="tx3"),
    ]
    result = derive_dispositions(entries)
    assert result[2] == Disposition.ACCEPTED, "UNDO row should be ACCEPTED, not SUPERSEDED"
    assert result[1] == Disposition.UNDONE
    assert result[3] == Disposition.ACCEPTED
