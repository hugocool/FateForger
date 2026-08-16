# src/memory/constraint_store.py
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from memory.constraint import Applicability, Constraint
from memory.models import Tier

_SCHEMA = """
CREATE TABLE IF NOT EXISTS constraints (
    uid          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL,
    necessity    TEXT NOT NULL,
    scope        TEXT NOT NULL,
    status       TEXT NOT NULL,
    source       TEXT NOT NULL,
    frame_slot   TEXT,
    tier         TEXT NOT NULL,
    applicability TEXT NOT NULL,
    source_observation_uids TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_constraints_tier ON constraints(tier);
"""


class ConstraintStore:
    """Persistence for derived constraints.

    Unlike the observation log this is NOT append-only: a constraint is a
    projection, so re-projecting replaces it in place. Its provenance
    (source_observation_uids) points back at the immutable log, which is
    where the history actually lives.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def upsert(self, constraint: Constraint) -> str:
        self._conn.execute(
            "INSERT INTO constraints (uid, name, description, necessity, scope, "
            " status, source, frame_slot, tier, applicability, "
            " source_observation_uids, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET "
            " name=excluded.name, description=excluded.description, "
            " necessity=excluded.necessity, scope=excluded.scope, "
            " status=excluded.status, source=excluded.source, "
            " frame_slot=excluded.frame_slot, tier=excluded.tier, "
            " applicability=excluded.applicability, "
            " source_observation_uids=excluded.source_observation_uids",
            (
                constraint.uid,
                constraint.name,
                constraint.description,
                constraint.necessity,
                constraint.scope,
                constraint.status,
                constraint.source,
                constraint.frame_slot,
                constraint.tier.value,
                constraint.applicability.model_dump_json(),
                json.dumps(constraint.source_observation_uids),
                constraint.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return constraint.uid

    def get(self, uid: str) -> Constraint | None:
        row = self._conn.execute(
            "SELECT * FROM constraints WHERE uid = ?", (uid,)
        ).fetchone()
        return self._row(row) if row else None

    def all(self) -> list[Constraint]:
        rows = self._conn.execute(
            "SELECT * FROM constraints ORDER BY created_at"
        ).fetchall()
        return [self._row(r) for r in rows]

    def durable(self) -> list[Constraint]:
        rows = self._conn.execute(
            "SELECT * FROM constraints WHERE tier = ? ORDER BY created_at",
            (Tier.DURABLE.value,),
        ).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> Constraint:
        return Constraint(
            uid=row["uid"],
            name=row["name"],
            description=row["description"],
            necessity=row["necessity"],
            scope=row["scope"],
            status=row["status"],
            source=row["source"],
            frame_slot=row["frame_slot"],
            tier=Tier(row["tier"]),
            applicability=Applicability.model_validate_json(row["applicability"]),
            source_observation_uids=json.loads(row["source_observation_uids"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
