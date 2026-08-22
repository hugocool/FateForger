"""Derive the disposition of each journal row from the rows themselves.

Hosts forget to report outcomes; the journal cannot. Deriving keeps the
training label honest, which matters because it feeds both the prompt
compiler and the constraint memory server.

Comparisons here are ``==`` rather than ``is``, deliberately. EntryKind and
PatchOutcome are str-enums, and an entry that reached this function without
pydantic validation -- ``model_construct``, or a row rebuilt from raw SQL,
where the column stores the enum *name* while the member carries its *value*
-- holds a plain ``str``. Identity then fails for every row, and because the
first check is ``outcome != APPLIED``, the whole batch silently derives
FAILED. Equality holds in both cases and costs nothing, so ``is`` bought
strictness the caller could not rely on and a failure mode nothing would
surface.
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

    # Only successful undos mark their targets as undone (Critical 2 fix).
    undone_tx = {
        e.undoes_tx
        for e in ordered
        if e.undoes_tx and e.outcome == PatchOutcome.APPLIED
    }

    # Only COMMIT rows determine supersession; UNDO rows don't supersede
    # (Critical 1 fix). Rename to clarify intent.
    later_commit_ids = [
        e.id
        for e in ordered
        if e.kind == EntryKind.COMMIT and e.id is not None
    ]
    last_commit_id = later_commit_ids[-1] if later_commit_ids else None

    result: dict[int, Disposition] = {}
    for entry in ordered:
        if entry.id is None:
            continue

        # Precedence order (Important 3 fix): evaluate in documented order.
        if entry.outcome != PatchOutcome.APPLIED:
            result[entry.id] = Disposition.FAILED
            continue

        # Rows whose tx_id was undone by a later UNDO are marked UNDONE.
        if entry.tx_id and entry.tx_id in undone_tx:
            result[entry.id] = Disposition.UNDONE
            continue

        # Only commits can be superseded by later commits. Gate prevents ATTEMPT/UNDO
        # rows reaching this check (Critical 3 fix).
        if entry.kind == EntryKind.COMMIT and last_commit_id is not None and entry.id < last_commit_id:
            result[entry.id] = Disposition.SUPERSEDED
            continue

        # Only attempts that weren't committed are abandoned.
        if entry.kind == EntryKind.ATTEMPT:
            result[entry.id] = Disposition.ABANDONED
            continue

        result[entry.id] = Disposition.ACCEPTED

    return result


__all__ = ["Disposition", "derive_dispositions"]
