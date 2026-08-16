# src/tmbx/journal/models.py
"""Journal row schema.

One row per patch attempt. Disposition is NOT stored — it is derived from
the rows (see ``tmbx.journal.disposition``), because hosts forget to report
and the journal cannot.
"""

from __future__ import annotations

import json
from datetime import date as date_type
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Field, SQLModel

JOURNAL_SCHEMA_VERSION = 1


class PatchOutcome(str, Enum):
    """Did the patch itself validate and apply?"""

    APPLIED = "applied"
    PARSE_FAILED = "parse_failed"
    APPLY_FAILED = "apply_failed"
    VALIDATION_FAILED = "validation_failed"


class EntryKind(str, Enum):
    """What produced this row."""

    ATTEMPT = "attempt"
    COMMIT = "commit"
    UNDO = "undo"


class ConstraintRef(BaseModel):
    """A constraint that was in context when the patch was produced.

    ``uid_kind`` records whether the uid was a real minted identifier or
    could not be resolved at all. There is no content-derived fallback:
    hashing a constraint's text to invent an identity is banned (CLAUDE.md)
    because it silently conflates distinct constraints that happen to read
    alike. A constraint without ``hints["uid"]`` gets ``uid_kind =
    "unresolvable"`` and an empty ``uid`` — an honest absence rather than a
    guessed key.
    """

    uid: str
    uid_kind: Literal["minted", "unresolvable"]
    reason: str | None = None


class JournalEntry(SQLModel, table=True):
    """One patch attempt, commit, or undo."""

    __tablename__ = "tmbx_journal"

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), index=True
    )
    kind: EntryKind = Field(default=EntryKind.ATTEMPT, index=True)

    calendar_id: str = Field(index=True)
    plan_date: date_type = Field(index=True)

    instruction: str | None = None
    constraints_json: str = Field(default="[]")

    ops_json: str = Field(default="{}")
    ops_schema_version: int = Field(default=JOURNAL_SCHEMA_VERSION)

    outcome: PatchOutcome = Field(default=PatchOutcome.APPLIED)
    error: str | None = None

    tx_id: str | None = Field(default=None, index=True)
    undoes_tx: str | None = Field(default=None, index=True)

    # Undo state. Populated on COMMIT rows only, so undo survives a restart —
    # holding it in a process-local dict is the defect behind #112.
    before_json: str | None = Field(
        default=None, description="Calendar events as they were before this commit"
    )
    post_etags_json: str | None = Field(
        default=None,
        description="Etags immediately after this commit wrote. Undo compares "
        "live state against these to refuse clobbering a newer edit.",
    )

    def set_constraints(self, refs: list[ConstraintRef]) -> None:
        """Serialise constraint refs into the JSON column."""
        self.constraints_json = json.dumps([r.model_dump() for r in refs])

    def get_constraints(self) -> list[ConstraintRef]:
        """Deserialise constraint refs from the JSON column."""
        raw = json.loads(self.constraints_json or "[]")
        return [ConstraintRef.model_validate(item) for item in raw]


__all__ = [
    "JOURNAL_SCHEMA_VERSION",
    "ConstraintRef",
    "EntryKind",
    "JournalEntry",
    "PatchOutcome",
]
