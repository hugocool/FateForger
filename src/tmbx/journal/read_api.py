# src/tmbx/journal/read_api.py
"""Read API over the journal — the feedback channel for constraint memory.

Consumers read this rather than the table, so storage can change underneath
them. Disposition is computed here, never stored.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta

from pydantic import BaseModel

from .disposition import Disposition, derive_dispositions
from .models import ConstraintRef
from .store import JournalStore


class PatchRecord(BaseModel):
    """One journal row, flattened, with its derived disposition."""

    id: int
    created_at: datetime
    kind: str
    calendar_id: str
    plan_date: date_type
    instruction: str | None
    constraints: list[ConstraintRef]
    ops_json: str
    ops_schema_version: int
    outcome: str
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

        Dispositions are derived per day, since supersession is a within-day
        relation.
        """
        out: list[PatchRecord] = []
        day = start
        while day <= end:
            entries = await self._store.by_day(calendar_id, day)
            dispositions = derive_dispositions(entries)
            for entry in entries:
                if entry.id is None:
                    continue
                out.append(
                    PatchRecord(
                        id=entry.id,
                        created_at=entry.created_at,
                        kind=entry.kind.value,
                        calendar_id=entry.calendar_id,
                        plan_date=entry.plan_date,
                        instruction=entry.instruction,
                        constraints=entry.get_constraints(),
                        ops_json=entry.ops_json,
                        ops_schema_version=entry.ops_schema_version,
                        outcome=entry.outcome.value,
                        disposition=dispositions[entry.id],
                        tx_id=entry.tx_id,
                        undoes_tx=entry.undoes_tx,
                    )
                )
            day = day + timedelta(days=1)
        return out


__all__ = ["JournalReader", "PatchRecord"]
