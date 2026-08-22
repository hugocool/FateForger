# src/memory/store.py
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from memory.migrations import apply_migrations
from memory.models import Channel, Observation, Provenance

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ObservationStore:
    """Append-only log of observations.

    I2: there is deliberately no update or delete. A correction is a new row.
    """

    def __init__(self, db_path: str) -> None:
        # check_same_thread=False because the MCP server dispatches sync tool
        # handlers to worker threads. This build's SQLite is multithread mode
        # (sqlite3.threadsafety == 1): a connection may cross threads only if
        # uses never overlap, so every method serialises on _lock. RLock, not
        # Lock — ConstraintStore._row calls observations_for re-entrantly.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        apply_migrations(self._conn)

    def append(self, obs: Observation) -> bool:
        """Append unless this uid is already present. True if a row was written.

        I5 says every write is compare-and-swap and never a blind replay of a
        prior payload. That was applied to L2 upserts and never to L1 appends,
        which is the half that was missing: a retry that mints a fresh uid and
        appends again IS a blind replay, and in an append-only log it is
        permanent. ON CONFLICT DO NOTHING makes the second attempt a no-op, so
        idempotency rests on exact equality of a system-minted identifier —
        explicitly outside the no-matching rule.
        """
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO observations "
                "(uid, text, channel, provenance, session_id, observed_at, anchors) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    obs.uid,
                    obs.text,
                    obs.channel.value,
                    obs.provenance.value,
                    obs.session_id,
                    obs.observed_at.isoformat(),
                    json.dumps(obs.anchors),
                ),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def get(self, uid: str) -> Observation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM observations WHERE uid = ?", (uid,)
            ).fetchone()
        return self._row_to_obs(row) if row else None

    def all(self) -> list[Observation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM observations ORDER BY observed_at"
            ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    def suppress(self, observation_uid: str, reason: str) -> None:
        """Record that this observation deliberately produced no constraint.

        Appended rather than written onto the observation: L1 is immutable
        (I2), and a suppression is a later judgement *about* a statement
        rather than a correction of it. Re-suppressing with a new reason
        replaces the old one -- the judgement is current, not historical, and
        an append-only list of reasons would need its own precedence rule for
        no benefit anyone has asked for.

        Without this, an observation that was deliberately not projected and
        one whose constraint was later removed by hand look identical: both
        simply have no row in `constraint_observations`. A rebuild cannot tell
        them apart, so it recreates exactly the constraints someone decided
        should not exist.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO observation_suppressions "
                "(observation_uid, reason, decided_at) VALUES (?, ?, ?) "
                "ON CONFLICT(observation_uid) DO UPDATE SET "
                "reason = excluded.reason, decided_at = excluded.decided_at",
                (observation_uid, reason, _now_iso()),
            )
            self._conn.commit()

    def suppressions(self) -> dict[str, str]:
        """Observation uid -> why it produced no constraint."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT observation_uid, reason FROM observation_suppressions"
            ).fetchall()
        return {r["observation_uid"]: r["reason"] for r in rows}

    def by_session(self, session_id: str) -> list[Observation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM observations WHERE session_id = ? ORDER BY observed_at",
                (session_id,),
            ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    @staticmethod
    def _row_to_obs(row: sqlite3.Row) -> Observation:
        return Observation(
            uid=row["uid"],
            text=row["text"],
            channel=Channel(row["channel"]),
            provenance=Provenance(row["provenance"]),
            session_id=row["session_id"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            anchors=json.loads(row["anchors"]),
        )
