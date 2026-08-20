# src/memory/anchor_store.py
from __future__ import annotations

import json
import sqlite3
import threading

from memory.anchor import Anchor, EdgeKind
from memory.migrations import apply_migrations

# Climb the taxonomy from the day's anchors and collect every constraint
# attached anywhere on the path.
#
# CROSS JOIN, not JOIN, and it is load-bearing. SQLite has no cardinality
# estimate for a recursive co-routine, assumes it is large, and inverts the
# join order: it scans the whole constraint_anchors table and probes the tiny
# reachable set. Measured at 100x the real store, on disk: 85 ms plain, 0.43 ms
# forced, with identical results. The plain form is linear in total store size
# and costs 0.87 ms today, so nothing would have caught it for a long time.
# See docs/superpowers/research/2026-08-19-substrate-benchmark.md.
#
# Depth is bounded inside the CTE rather than by the caller: a cycle introduced
# by a bad induction would otherwise spin, and the gate that would prevent such
# an edge does not exist yet (#140).
_WALK = """
WITH RECURSIVE reachable(uid, depth) AS (
    SELECT value, 0 FROM json_each(?)
    UNION
    SELECT e.parent_uid, r.depth + 1
      FROM anchor_edges e JOIN reachable r ON e.child_uid = r.uid
     WHERE r.depth < ?
)
SELECT DISTINCT ca.constraint_uid
  FROM reachable r
  CROSS JOIN constraint_anchors ca ON ca.anchor_uid = r.uid
"""

# How far a rule stated generally may reach down to a specific activity. Three
# is what the measured taxonomy shape bottoms out at; the benchmark showed
# latency flat past it, so the bound costs nothing and stops a cycle.
MAX_WALK_DEPTH = 3


class AnchorStore:
    """The anchor taxonomy and its links to constraints.

    Shares the database file with the observation and constraint stores, so a
    walk joins all three without crossing a process boundary — which is what
    #141's measurement bought.
    """

    def __init__(self, db_path: str) -> None:
        # check_same_thread=False with an RLock, matching the sibling stores:
        # this build's SQLite is multithread mode, so a connection may cross
        # threads only if uses never overlap.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        apply_migrations(self._conn)

    def upsert(self, anchor: Anchor) -> str:
        with self._lock:
            self._conn.execute(
                "INSERT INTO anchors (uid, name) VALUES (?,?) "
                "ON CONFLICT(uid) DO UPDATE SET name=excluded.name",
                (anchor.uid, anchor.name),
            )
            self._conn.commit()
        return anchor.uid

    def get(self, uid: str) -> Anchor | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM anchors WHERE uid = ?", (uid,)
            ).fetchone()
        return Anchor(uid=row["uid"], name=row["name"]) if row else None

    def all(self) -> list[Anchor]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM anchors ORDER BY name"
            ).fetchall()
        return [Anchor(uid=r["uid"], name=r["name"]) for r in rows]

    def add_edge(self, parent_uid: str, child_uid: str, kind: EdgeKind) -> None:
        """Record that child is a kind of / part of parent.

        Both endpoints must already exist. An edge to an unknown anchor would
        make the walk silently traverse nothing, which is indistinguishable
        from a rule that does not apply.
        """
        if parent_uid == child_uid:
            raise ValueError(
                f"refusing a self-edge on anchor {parent_uid!r}: the walk "
                f"bounds depth but a self-edge makes every hop a no-op"
            )
        for uid in (parent_uid, child_uid):
            if self.get(uid) is None:
                raise ValueError(
                    f"edge endpoint {uid!r} is not a known anchor; an edge "
                    f"into nothing makes the walk quietly skip rules"
                )
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO anchor_edges "
                "(parent_uid, child_uid, kind) VALUES (?,?,?)",
                (parent_uid, child_uid, kind.value),
            )
            self._conn.commit()

    def replace_constraint_links(
        self, constraint_uid: str, anchor_uids: list[str]
    ) -> None:
        """Set a constraint's anchors to exactly these.

        Replace rather than append, for the same reason provenance links are
        replaced: re-projection can drop an anchor as well as add one, and an
        add-only table would keep serving a rule from an anchor the current
        judgement no longer associates it with.
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM constraint_anchors WHERE constraint_uid = ?",
                    (constraint_uid,),
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO constraint_anchors "
                    "(constraint_uid, anchor_uid) VALUES (?,?)",
                    [(constraint_uid, uid) for uid in anchor_uids],
                )

    def anchors_for(self, constraint_uid: str) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT anchor_uid FROM constraint_anchors "
                "WHERE constraint_uid = ? ORDER BY anchor_uid",
                (constraint_uid,),
            ).fetchall()
        return [r["anchor_uid"] for r in rows]

    def constraints_reachable_from(
        self, seed_uids: list[str], max_depth: int = MAX_WALK_DEPTH
    ) -> set[str]:
        """Constraint uids reachable from these anchors within max_depth hops.

        Pure structure — a graph walk and set membership over system-minted
        uids. No model, so this is callable from the read path.
        """
        if not seed_uids:
            return set()
        with self._lock:
            rows = self._conn.execute(
                _WALK, (json.dumps(seed_uids), max_depth)
            ).fetchall()
        return {r["constraint_uid"] for r in rows}
