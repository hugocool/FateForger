# src/memory/migrations.py
from __future__ import annotations

import sqlite3

# The version this build of the code expects. Bump it in the same commit that
# appends to _MIGRATIONS, never separately.
SCHEMA_VERSION = 3

# Version 1 is the shape that shipped before versioning existed. It is
# reproduced here verbatim rather than being re-derived, because an existing
# store must baseline onto it without any statement running.
_V1 = """
CREATE TABLE IF NOT EXISTS observations (
    uid          TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    channel      TEXT NOT NULL,
    provenance   TEXT NOT NULL,
    session_id   TEXT,
    observed_at  TEXT NOT NULL,
    anchors      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs_session ON observations(session_id);
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
    created_at   TEXT NOT NULL,
    decay_class      TEXT NOT NULL,
    last_observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_constraints_tier ON constraints(tier);
CREATE TABLE IF NOT EXISTS constraint_observations (
    constraint_uid  TEXT NOT NULL,
    observation_uid TEXT NOT NULL,
    PRIMARY KEY (constraint_uid, observation_uid)
);
CREATE INDEX IF NOT EXISTS ix_co_observation
    ON constraint_observations(observation_uid);
"""

# Version 2 adds the anchor graph: a pure taxonomy of anchors, and an edge
# table joining constraints to it. The shape is #137's decision — conditionality
# in typed edges rather than predicates — and the substrate is #141's, measured
# rather than preferred.
_V2 = """
CREATE TABLE IF NOT EXISTS anchors (
    uid  TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS anchor_edges (
    parent_uid TEXT NOT NULL,
    child_uid  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    PRIMARY KEY (parent_uid, child_uid, kind)
);
CREATE INDEX IF NOT EXISTS ix_edge_child  ON anchor_edges(child_uid);
CREATE INDEX IF NOT EXISTS ix_edge_parent ON anchor_edges(parent_uid);
CREATE TABLE IF NOT EXISTS constraint_anchors (
    constraint_uid TEXT NOT NULL,
    anchor_uid     TEXT NOT NULL,
    PRIMARY KEY (constraint_uid, anchor_uid)
);
CREATE INDEX IF NOT EXISTS ix_ca_anchor ON constraint_anchors(anchor_uid);
"""

# version -> DDL applied to reach it. Append only; never edit a shipped entry,
# because a store that already ran it will not run it again.
# Version 3 records *why* an observation produced no constraint. ingest has
# always decided this -- meta, generated, a restatement of something already
# held -- and always thrown the answer away, so an observation that was
# deliberately not projected and one whose constraint was later removed by hand
# were indistinguishable: both simply have no row in constraint_observations.
#
# A separate table rather than a column, because L1 is append-only (I2). The
# observation is never rewritten; a suppression is a later judgement *about* it
# and is appended alongside.
_V3 = """
CREATE TABLE IF NOT EXISTS observation_suppressions (
    observation_uid TEXT PRIMARY KEY,
    reason          TEXT NOT NULL,
    decided_at      TEXT NOT NULL
);
"""

_MIGRATIONS: dict[int, str] = {1: _V1, 2: _V2, 3: _V3}

# What each version must end up with. Checked after migrating, so a store whose
# stamp disagrees with its actual shape fails loudly at connect time instead of
# as an IndexError inside a row mapper on the first read.
_EXPECTED_COLUMNS: dict[int, dict[str, set[str]]] = {
    1: {
        "observations": {
            "uid", "text", "channel", "provenance",
            "session_id", "observed_at", "anchors",
        },
        "constraints": {
            "uid", "name", "description", "necessity", "scope", "status",
            "source", "frame_slot", "tier", "applicability", "created_at",
            "decay_class", "last_observed_at",
        },
        "constraint_observations": {"constraint_uid", "observation_uid"},
    },
    2: {
        "anchors": {"uid", "name"},
        "anchor_edges": {"parent_uid", "child_uid", "kind"},
        "constraint_anchors": {"constraint_uid", "anchor_uid"},
    },
    3: {
        "observation_suppressions": {"observation_uid", "reason", "decided_at"},
    },
}


class SchemaTooNew(RuntimeError):
    """The database was written by a newer build than this one."""


class SchemaMismatch(RuntimeError):
    """The stamped version disagrees with the columns actually present."""


def _user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    # table is a name this module minted, not user content, so interpolating
    # it is identifier handling rather than a judgement. PRAGMA takes no
    # bound parameters.
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _verify(conn: sqlite3.Connection, version: int) -> None:
    """Check every version's tables, not only the newest one.

    Each entry lists what that version ADDED, so checking only the highest
    would stop verifying v1's columns the moment v2 shipped — the exact gap
    #155 exists to close, reintroduced one migration later.
    """
    expected = {
        table: columns
        for v in range(1, version + 1)
        for table, columns in _EXPECTED_COLUMNS.get(v, {}).items()
    }
    if not expected:
        return
    present = _table_names(conn)
    for table, columns in expected.items():
        if table not in present:
            raise SchemaMismatch(
                f"database is stamped schema version {version} but table "
                f"{table!r} is missing; the store is not the shape this build "
                f"expects and reads would fail deep inside a row mapper"
            )
        missing = columns - _columns(conn, table)
        if missing:
            raise SchemaMismatch(
                f"database is stamped schema version {version} but table "
                f"{table!r} is missing column(s) {sorted(missing)}; a store "
                f"predating a column is not upgraded by CREATE TABLE IF NOT "
                f"EXISTS, so this needs a migration entry rather than a retry"
            )


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Bring a connection's database up to SCHEMA_VERSION, and verify it.

    Both stores open the same file, so both call this and it must be safe to
    run twice: the version stamp makes the second call a no-op rather than a
    re-application.

    Version 0 means either a fresh file or a store predating versioning. Both
    take the same path deliberately: v1 *is* the pre-versioning schema and its
    DDL is entirely CREATE ... IF NOT EXISTS, so replaying it over a populated
    legacy store is a no-op that ends with the correct stamp. An earlier draft
    special-cased the legacy store; no test could tell the two paths apart,
    which is what a redundant branch looks like from the outside.
    """
    version = _user_version(conn)

    if version > SCHEMA_VERSION:
        raise SchemaTooNew(
            f"database is at schema version {version} but this build "
            f"understands at most {SCHEMA_VERSION}; refusing to touch it, "
            f"because a newer build may have added columns whose absence "
            f"from this build's writes would silently lose data"
        )

    while version < SCHEMA_VERSION:
        target = version + 1
        # Not wrapped in `with conn:` — executescript commits any open
        # transaction before it runs, so the wrapper would imply an atomicity
        # it cannot deliver. Every migration must therefore be written to be
        # re-runnable: if the process dies between the DDL and the stamp, the
        # next start replays the same step against a database that already
        # has part of it. The stamp is written last for that reason.
        conn.executescript(_MIGRATIONS[target])
        conn.execute(f"PRAGMA user_version = {target}")
        conn.commit()
        version = target

    _verify(conn, version)
    return version
