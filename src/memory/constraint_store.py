# src/memory/constraint_store.py
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from memory.constraint import Applicability, Constraint
from memory.migrations import apply_migrations
from memory.models import DecayClass, Tier

class ConstraintStore:
    """Persistence for derived constraints.

    Unlike the observation log this is NOT append-only: a constraint is a
    projection, so re-projecting replaces it in place. Its provenance
    (source_observation_uids) points back at the immutable log, which is
    where the history actually lives.
    """

    def __init__(self, db_path: str) -> None:
        # check_same_thread=False because the MCP server dispatches sync tool
        # handlers to worker threads. This build's SQLite is multithread mode
        # (sqlite3.threadsafety == 1): a connection may cross threads only if
        # uses never overlap, so every method serialises on _lock. RLock, not
        # Lock — _row calls observations_for re-entrantly.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        apply_migrations(self._conn)

    def upsert(self, constraint: Constraint) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT INTO constraints (uid, name, description, necessity, scope, "
                " status, source, frame_slot, tier, applicability, created_at, "
                " decay_class, last_observed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET "
                " name=excluded.name, description=excluded.description, "
                " necessity=excluded.necessity, scope=excluded.scope, "
                " status=excluded.status, source=excluded.source, "
                " frame_slot=excluded.frame_slot, tier=excluded.tier, "
                " applicability=excluded.applicability, "
                " created_at=excluded.created_at, "
                " decay_class=excluded.decay_class, "
                " last_observed_at=excluded.last_observed_at",
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
                    constraint.created_at.isoformat(),
                    constraint.decay_class.value,
                    constraint.last_observed_at.isoformat(),
                ),
            )
            self._conn.commit()
            # Re-projection can DROP an observation, not only add one, so set the
            # links rather than appending to them. link_observation remains for the
            # incremental fold path, where adding is exactly what is meant.
            self.replace_links(constraint.uid, constraint.source_observation_uids)
        return constraint.uid

    def link_observation(self, constraint_uid: str, observation_uid: str) -> None:
        """Record that an observation contributed to a constraint.

        Idempotent by primary key, so this replaces a read-modify-write on a
        list field: an append is one insert that replays no prior payload,
        which is what compare-and-swap is for. The reverse index also gives
        re-projection an observation -> constraint lookup.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO constraint_observations "
                "(constraint_uid, observation_uid) VALUES (?,?)",
                (constraint_uid, observation_uid),
            )
            self._conn.commit()

    def replace_links(self, constraint_uid: str, observation_uids: list[str]) -> None:
        """Set a constraint's provenance to exactly these observations.

        Re-projection can DROP an observation from a constraint, not only add
        one, so an add-only link table would silently over-report provenance
        and inflate the evidence counts that promotion and decay rely on.
        Delete-then-insert in a single transaction so a concurrent reader
        never observes a constraint with no provenance at all.
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM constraint_observations WHERE constraint_uid = ?",
                    (constraint_uid,),
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO constraint_observations "
                    "(constraint_uid, observation_uid) VALUES (?,?)",
                    [(constraint_uid, uid) for uid in observation_uids],
                )

    def observations_for(self, constraint_uid: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT observation_uid FROM constraint_observations "
                "WHERE constraint_uid = ? ORDER BY observation_uid",
                (constraint_uid,),
            ).fetchall()
        return [r["observation_uid"] for r in rows]

    def get(self, uid: str) -> Constraint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM constraints WHERE uid = ?", (uid,)
            ).fetchone()
        return self._row(row) if row else None

    def all(self) -> list[Constraint]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM constraints ORDER BY created_at"
            ).fetchall()
        return [self._row(r) for r in rows]

    def durable(self) -> list[Constraint]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM constraints WHERE tier = ? ORDER BY created_at",
                (Tier.DURABLE.value,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def _row(self, row: sqlite3.Row) -> Constraint:
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
            source_observation_uids=self.observations_for(row["uid"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            decay_class=DecayClass(row["decay_class"]),
            last_observed_at=datetime.fromisoformat(row["last_observed_at"]),
        )
