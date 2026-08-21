# What sits under the graph — measured (#141)

**Answer: SQLite, with recursive CTEs. No graph database.**

#141 asked to decide with a measurement rather than a preference, and reopened
a cheaper question first after Kuzu was found archived: at hundreds of concepts
and thousands of records, does a bounded typed walk need a graph database at
all? It does not, by two orders of magnitude.

## What was measured

The shape #137 decided: a pure anchor taxonomy (`is_a` / `part_of`) plus the
constraint table joined through an edge table carrying offsets, `day_offset`
and bi-temporal validity.

The walk is #135's — today's calendar supplies seed anchors, climb the taxonomy
to reach rules stated more generally ("sport" covering hockey), collect every
constraint attached to any reachable anchor that is valid on the day. Reported
as p50/p95 of a whole walk, because callers hold it inside a planning loop and
a mean would hide the tail that gets felt.

Script: `scripts/dev/bench_substrate_walk.py`.

## Result, on disk, at 100x the real store

| depth | seed anchors | p50 | p95 | max | rows |
|---|---|---|---|---|---|
| 3 | 6 | 0.79 ms | 0.97 ms | 1.51 ms | 150 |
| 3 | 20 | 2.37 ms | 2.96 ms | 3.49 ms | 518 |
| 5 | 6 | 0.81 ms | 1.09 ms | 4.02 ms | 166 |
| 8 | 20 | 2.46 ms | 3.00 ms | 9.50 ms | 525 |

170,000 constraints and 30,000 anchors occupy 28.8 MB, so the real store is
about 0.3 MB. Latency is flat in depth — the taxonomy bottoms out well before
the bound — and linear in the number of seed anchors, which is the correct
shape: more seeds is genuinely more subgraph.

There is no budget here worth defending against a network hop. A Neo4j round
trip on localhost costs more than the entire walk.

## The finding that nearly hid this

The obvious query is linear in total store size, and looks completely fine at
today's volume.

| scale | plain `JOIN` | `CROSS JOIN` |
|---|---|---|
| real (1,700) | 0.87 ms | 0.23 ms |
| 10x | 8.0 ms | 0.25 ms |
| 100x (170,000) | 85 ms | 0.43 ms |

`EXPLAIN QUERY PLAN` shows why: `SCAN ca`. SQLite has no cardinality estimate
for a recursive co-routine, assumes it is large, and inverts the join — it
scans all 226,000 edge rows and probes the ~24-row reachable set. `CROSS JOIN`
pins the outer loop. The results are identical; it is a planner hint, not a
semantic change. `ANALYZE` does not fix it.

Worth stating plainly because of how this would have played out: at today's
volume the wrong plan costs 0.87 ms and passes any test anyone would write.
The linear term only becomes visible at a scale the store reaches later, and
the conclusion drawn from it would have been "the embedded option does not
scale, we need a graph database" — a substrate decision made on a missing
keyword.

## What this buys

- **The server stays genuinely standalone.** No second process, no network
  hop, no credentials. #140's replay gate wants many isolated graphs in CI,
  which is painful against a shared server and trivial against a file.
- **One store, one file.** Observations, constraints and the graph share the
  database that already exists, so a walk joins them without crossing a
  process boundary.
- **The read path stays synchronous and model-free**, which the AST guard
  already enforces.

## What was not measured, and why it does not change the answer

Neo4j was not benchmarked. It is in the compose file but not running, and the
measurement above makes the comparison moot: the walk costs less than a
localhost round trip, so Neo4j cannot win on latency and loses on
standalone-ness. It would take a requirement other than speed — one nobody has
stated — to reopen this.

Cold-cache behaviour was not isolated. The server is long-running and the
working set is under a megabyte at real volume, so the warm path is the honest
one to measure.
