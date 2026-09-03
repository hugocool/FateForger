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

    def __init__(
        self,
        db_path: str,
        *,
        conn: sqlite3.Connection | None = None,
        lock: threading.RLock | None = None,
    ) -> None:
        """Open the store, or adopt a connection a sibling already opened.

        Pass `conn` and `lock` to share one connection with sibling stores over
        the same file. `MemoryService` does, because `observe` writes an
        observation through one store and projects a constraint through
        another: on separate connections there is no transaction that can span
        the two, and a crash between them leaves an observation with no
        constraint derived from it (#186). Sharing is the prerequisite the
        transaction boundary needs; it does not by itself make ingest atomic.

        The lock travels with the connection and is not optional. This build's
        SQLite is multithread mode, so one connection may cross threads only if
        uses never overlap -- two stores serialising on two different locks
        over one connection is exactly the overlap that rule forbids.
        """
        # check_same_thread=False because the MCP server dispatches sync tool
        # handlers to worker threads. This build's SQLite is multithread mode
        # (sqlite3.threadsafety == 1): a connection may cross threads only if
        # uses never overlap, so every method serialises on _lock. RLock, not
        # Lock — _row calls observations_for re-entrantly.
        if (conn is None) != (lock is None):
            raise ValueError(
                "conn and lock are shared together or not at all; one "
                "without the other lets two stores overlap on one connection"
            )
        self._shared = conn is not None
        self._conn = conn or sqlite3.connect(db_path, check_same_thread=False)
        self._lock = lock or threading.RLock()
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

    def constraint_for_observation(self, observation_uid: str) -> str | None:
        """Which constraint an observation produced, if projection got that far.

        None means the observation is an orphan: stored, but its projection
        never completed. ix_co_observation exists for this direction.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT constraint_uid FROM constraint_observations "
                "WHERE observation_uid = ? LIMIT 1",
                (observation_uid,),
            ).fetchone()
        return row["constraint_uid"] if row else None

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

    def for_session(self, session_id: str) -> list[Constraint]:
        """Session-tier constraints belonging to one conversation.

        A `Constraint` carries no `session_id` — only an `Observation` does —
        so the scoping comes through provenance rather than through a column.
        That is the right place for it: identity is minted (I3) and the link
        back to what was said is exactly what `constraint_observations`
        exists to hold. It also means no schema change, and no second copy of
        a fact the store already knows.

        DISTINCT because a constraint folds several observations and more than
        one of them may belong to this session.

        Ordered by creation so a caller reading them back gets the shape of
        the conversation rather than an arbitrary permutation.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT c.* FROM constraints c "
                "JOIN constraint_observations co ON co.constraint_uid = c.uid "
                "JOIN observations o ON o.uid = co.observation_uid "
                "WHERE c.tier = ? AND o.session_id = ? "
                "ORDER BY c.created_at",
                (Tier.SESSION.value, session_id),
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
