"""Derive the disposition of each journal row from the rows themselves.

Hosts forget to report outcomes; the journal cannot. Deriving keeps the
training label honest, which matters because it feeds both the prompt
compiler and the constraint memory server.
"""

from __future__ import annotations

from enum import Enum

from .models import EntryKind, JournalEntry, PatchOutcome


class Disposition(str, Enum):
    ACCEPTED = "accepted"
    UNDONE = "undone"
    SUPERSEDED = "superseded"
    ABANDONED = "abandoned"
    FAILED = "failed"


def derive_dispositions(entries: list[JournalEntry]) -> dict[int, Disposition]:
    """Map entry id → disposition for one calendar-day's rows.

    Args:
        entries: Rows for a single ``(calendar_id, plan_date)``, any order.

    Returns:
        Dict keyed by entry id. Precedence: failed → undone → superseded →
        abandoned → accepted.
    """
    ordered = sorted(entries, key=lambda e: (e.id or 0))
    undone_tx = {e.undoes_tx for e in ordered if e.undoes_tx}

    commit_ids = [
        e.id
        for e in ordered
        if e.kind in (EntryKind.COMMIT, EntryKind.UNDO) and e.id is not None
    ]
    last_commit_id = commit_ids[-1] if commit_ids else None

    result: dict[int, Disposition] = {}
    for entry in ordered:
        if entry.id is None:
            continue

        if entry.outcome is not PatchOutcome.APPLIED:
            result[entry.id] = Disposition.FAILED
            continue

        if entry.kind is EntryKind.ATTEMPT:
            result[entry.id] = Disposition.ABANDONED
            continue

        if entry.tx_id and entry.tx_id in undone_tx:
            result[entry.id] = Disposition.UNDONE
            continue

        if last_commit_id is not None and entry.id < last_commit_id:
            result[entry.id] = Disposition.SUPERSEDED
            continue

        result[entry.id] = Disposition.ACCEPTED

    return result


__all__ = ["Disposition", "derive_dispositions"]
