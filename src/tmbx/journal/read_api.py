# src/tmbx/journal/read_api.py
"""Read API over the journal — the feedback channel for constraint memory.

Consumers read this rather than the table, so storage can change underneath
them. Disposition is computed here, never stored.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel

from .disposition import Disposition, derive_dispositions
from .models import ConstraintRef, EntryKind, JournalEntry, PatchOutcome
from .store import JournalStore


class PatchRecord(BaseModel):
    """One journal row, flattened, with its derived disposition."""

    id: int
    created_at: datetime
    kind: EntryKind
    calendar_id: str
    plan_date: date_type
    instruction: str | None
    constraints: list[ConstraintRef]
    ops_json: str
    ops_schema_version: int
    outcome: PatchOutcome
    error: str | None
    disposition: Disposition
    tx_id: str | None
    undoes_tx: str | None


class JournalReader:
    """Read journal rows with dispositions resolved."""

    def __init__(self, store: JournalStore) -> None:
        self._store = store

    async def records(
        self, calendar_id: str, start: date_type, end: date_type
    ) -> list[PatchRecord]:
        """Return records for an inclusive date range.

        Fetches the whole range in a single query, then groups by
        ``plan_date`` before deriving dispositions — one call to
        ``derive_dispositions`` per day, never one over the concatenated
        range, because supersession is a within-day relation. Keep this
        grouping explicit here rather than letting the single query make it
        implicit: a regression that derived once over the whole range would
        produce identical output on any fixture that only ever populates one
        day, so the per-day boundary must stay visible at the call site.
        """
        entries = await self._store.by_range(calendar_id, start, end)

        by_day: dict[date_type, list[JournalEntry]] = {}
        for entry in entries:
            by_day.setdefault(entry.plan_date, []).append(entry)

        out: list[PatchRecord] = []
        for day in sorted(by_day):
            day_entries = by_day[day]
            dispositions = derive_dispositions(day_entries)
            for entry in day_entries:
                if entry.id is None:
                    continue
                out.append(
                    PatchRecord(
                        id=entry.id,
                        created_at=entry.created_at,
                        kind=entry.kind,
                        calendar_id=entry.calendar_id,
                        plan_date=entry.plan_date,
                        instruction=entry.instruction,
                        constraints=entry.get_constraints(),
                        ops_json=entry.ops_json,
                        ops_schema_version=entry.ops_schema_version,
                        outcome=entry.outcome,
                        error=entry.error,
                        disposition=dispositions[entry.id],
                        tx_id=entry.tx_id,
                        undoes_tx=entry.undoes_tx,
                    )
                )
        return out


__all__ = ["JournalReader", "PatchRecord"]
