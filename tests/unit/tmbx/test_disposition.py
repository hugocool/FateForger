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


# --- #177: ACCEPTED is the terminal fallback, not a ratification -----------
#
# derive_dispositions ends with an unguarded `result[entry.id] = ACCEPTED`
# after FAILED -> UNDONE -> SUPERSEDED -> ABANDONED. So any applied, un-undone,
# latest COMMIT is ACCEPTED. That only ever *meant* "the user approved this"
# because a human gate stood upstream of every commit; 2360304 made
# StageReviewCommitNode auto-commit, and the label kept its old name.
#
# The disposition is a training label -- disposition.py's own docstring says it
# "feeds both the prompt compiler and the constraint memory server" -- so an
# unratified commit now teaches the memory server that Hugo approved a plan he
# was never asked about. The memory design rejects exactly this inference:
# Reliability is three-valued because silence is not evidence.
#
# These cannot be written as "build an unattended row and check its
# disposition", because JournalEntry has no field that can say a row was
# unattended. That absence is the defect. The fix has to reach the schema
# before it can reach the derivation.


def test_journal_entry_can_record_whether_a_human_ratified_a_commit():
    """#177 -- the schema must be able to say 'nobody ruled on this'.

    Strict xfail: when a ratification field lands this XPASSes and fails,
    which is the signal to delete the marker rather than leave a passing
    test that no longer asserts anything.
    """
    import pytest

    field_names = set(JournalEntry.model_fields)
    ratification_fields = field_names & {
        "ratified",
        "ratified_by",
        "ratified_at",
        "approval",
        "human_ruling",
    }
    if not ratification_fields:
        pytest.xfail(
            "JournalEntry carries no ratification field, so an auto-committed "
            "row is indistinguishable from one the user approved (#177)"
        )


def test_a_lone_commit_is_accepted_without_any_evidence_of_approval():
    """Pins the defect rather than the intent, so the gap stays visible.

    This asserts today's behaviour on purpose. A commit derives ACCEPTED with
    nothing anywhere recording that a human saw it -- there is no field to
    record it in. When #177 lands, this test should change to expect
    UNRATIFIED, and the change should be deliberate rather than incidental.
    """
    entries = [_entry(1, EntryKind.COMMIT, tx_id="tx1")]
    assert derive_dispositions(entries) == {1: Disposition.ACCEPTED}


def test_dispositions_survive_an_entry_that_skipped_validation():
    """A str-typed outcome must not turn every row into FAILED.

    EntryKind and PatchOutcome are str-enums. An entry built without pydantic
    validation -- model_construct, or a row reassembled from raw SQL, where
    the column stores the enum name while the member carries its value --
    holds a plain str. Under identity comparison the first check
    (outcome != APPLIED) matched every row and the whole batch derived FAILED,
    silently and in a direction that looks like a real result.

    Found while reading the live journal: 173 real rows, every one of which
    would have been reported as FAILED by a raw-SQL reader.
    """
    entry = JournalEntry.model_construct(
        id=1,
        kind="COMMIT",
        calendar_id="primary",
        plan_date=DAY,
        ops_json="{}",
        outcome="applied",
        tx_id="tx1",
        undoes_tx=None,
    )
    assert derive_dispositions([entry]) == {1: Disposition.ACCEPTED}
