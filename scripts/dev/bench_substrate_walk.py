"""#141: does a bounded typed walk need a graph database?

Shape is #137's decision: a pure anchor taxonomy (is_a / part_of) plus the
constraint table joined through an edge table carrying offsets, day_offset and
bi-temporal validity.

The walk is #135's: today's calendar supplies seed anchors, we climb the
taxonomy to find rules stated at a more general level ("sport" covering
hockey), and collect every constraint attached to any reachable anchor that is
valid on the day. Two to three hops, typed, bounded.

Measured as p50/p95 of one whole walk, because callers hold it inside a
planning loop — a mean would hide the tail that actually gets felt.
"""
from __future__ import annotations

import random
import sqlite3
import statistics
import time
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE anchors (uid TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE anchor_edges (
    parent_uid TEXT NOT NULL, child_uid TEXT NOT NULL, kind TEXT NOT NULL,
    PRIMARY KEY (parent_uid, child_uid, kind)
);
CREATE INDEX ix_edge_child  ON anchor_edges(child_uid);
CREATE INDEX ix_edge_parent ON anchor_edges(parent_uid);
CREATE TABLE constraints (
    uid TEXT PRIMARY KEY, name TEXT NOT NULL, necessity TEXT NOT NULL,
    tier TEXT NOT NULL
);
CREATE TABLE constraint_anchors (
    constraint_uid TEXT NOT NULL, anchor_uid TEXT NOT NULL,
    offset_minutes INTEGER, day_offset INTEGER NOT NULL DEFAULT 0,
    valid_from TEXT NOT NULL, valid_to TEXT
);
CREATE INDEX ix_ca_anchor ON constraint_anchors(anchor_uid);
"""

# Climb the taxonomy from the day's anchors, then collect rules attached
# anywhere on the path. Depth is bounded in the CTE itself rather than by the
# caller: an is_a cycle introduced by a bad induction would otherwise spin.
WALK = """
WITH RECURSIVE reachable(uid, depth) AS (
    SELECT value, 0 FROM json_each(?)
    UNION
    SELECT e.parent_uid, r.depth + 1
      FROM anchor_edges e JOIN reachable r ON e.child_uid = r.uid
     WHERE r.depth < ? AND e.kind IN ('is_a', 'part_of')
)
SELECT DISTINCT c.uid, c.name, c.necessity, ca.offset_minutes, ca.day_offset
  FROM reachable r
  -- CROSS JOIN, not JOIN. SQLite has no cardinality estimate for a recursive
  -- co-routine, assumes it is large, and inverts the join: it scans all of
  -- constraint_anchors and probes the ~24-row reachable set. CROSS JOIN fixes
  -- the outer loop without changing the result. Measured at 100x real volume:
  -- 85 ms plain, 0.43 ms forced. It reads as a micro-optimisation and is the
  -- difference between linear in store size and flat.
  CROSS JOIN constraint_anchors ca ON ca.anchor_uid = r.uid
  JOIN constraints c         ON c.uid = ca.constraint_uid
 WHERE c.tier = 'durable'
   AND ca.valid_from <= ?
   AND (ca.valid_to IS NULL OR ca.valid_to > ?)
"""


def build(conn, n_constraints, n_anchors, seed=17):
    rng = random.Random(seed)
    conn.executescript(SCHEMA)
    anchors = [f"a{i:07d}" for i in range(n_anchors)]
    conn.executemany(
        "INSERT INTO anchors VALUES (?,?)",
        [(a, f"anchor {i}") for i, a in enumerate(anchors)],
    )
    # A taxonomy shaped like a real one: a handful of roots, fan-out ~5, most
    # concepts near the leaves. Every node gets a parent from an earlier slice,
    # which keeps it acyclic and roughly 4-6 deep at these sizes.
    edges = []
    for i, child in enumerate(anchors):
        if i < 8:
            continue
        parent = anchors[rng.randrange(0, max(1, i // 5))]
        edges.append((parent, child, "is_a" if i % 4 else "part_of"))
    conn.executemany("INSERT OR IGNORE INTO anchor_edges VALUES (?,?,?)", edges)

    today = date(2026, 8, 19)
    conn.executemany(
        "INSERT INTO constraints VALUES (?,?,?,?)",
        [
            (f"c{i:07d}", f"rule {i}", "must" if i % 8 == 0 else "should", "durable")
            for i in range(n_constraints)
        ],
    )
    links = []
    for i in range(n_constraints):
        # Most rules hang off one anchor; some off two, as real ones do.
        for _ in range(1 if i % 3 else 2):
            links.append(
                (
                    f"c{i:07d}",
                    anchors[rng.randrange(n_anchors)],
                    rng.choice([None, -120, -30, 15, 60]),
                    rng.choice([0, 0, 0, -1, 1]),
                    (today - timedelta(days=rng.randrange(1, 900))).isoformat(),
                    None if i % 5 else (today + timedelta(days=90)).isoformat(),
                )
            )
    conn.executemany(
        "INSERT INTO constraint_anchors VALUES (?,?,?,?,?,?)", links
    )
    conn.commit()
    return anchors


def bench(n_constraints, n_anchors, depth=3, trials=300, seeds_per_day=6):
    conn = sqlite3.connect(":memory:")
    anchors = build(conn, n_constraints, n_anchors)
    rng = random.Random(99)
    day = "2026-08-19"
    import json

    timings, rows_seen = [], []
    for _ in range(trials):
        seeds = json.dumps(
            [anchors[rng.randrange(n_anchors)] for _ in range(seeds_per_day)]
        )
        t0 = time.perf_counter()
        rows = conn.execute(WALK, (seeds, depth, day, day)).fetchall()
        timings.append((time.perf_counter() - t0) * 1000)
        rows_seen.append(len(rows))
    conn.close()
    timings.sort()
    return {
        "p50": statistics.median(timings),
        "p95": timings[int(len(timings) * 0.95)],
        "max": timings[-1],
        "rows": statistics.median(rows_seen),
    }


if __name__ == "__main__":
    print(f"{'scale':<26}{'constraints':>12}{'anchors':>9}"
          f"{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}{'rows':>7}")
    for label, nc, na in [
        ("real store today", 1_700, 300),
        ("10x", 17_000, 3_000),
        ("100x", 170_000, 30_000),
    ]:
        t0 = time.perf_counter()
        r = bench(nc, na)
        print(f"{label:<26}{nc:>12,}{na:>9,}"
              f"{r['p50']:>9.3f}{r['p95']:>9.3f}{r['max']:>9.3f}{r['rows']:>7.0f}"
              f"   (build+bench {time.perf_counter()-t0:.1f}s)", flush=True)
