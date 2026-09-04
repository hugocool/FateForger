# tests/memory/test_migrations.py
"""The schema-version ladder (#155).

The gap these cover: CREATE TABLE IF NOT EXISTS never adds a column, so a
store predating a column stayed silently wrong until a row mapper raised
IndexError. Every test here is about a database OLDER than the code, which is
the case no earlier test could reach because every run re-seeded from scratch.
"""
from __future__ import annotations

import sqlite3

import pytest

from memory.constraint_store import ConstraintStore
from memory.migrations import (
    SCHEMA_VERSION,
    SchemaMismatch,
    SchemaTooNew,
    apply_migrations,
)
from memory.store import ObservationStore


def _version(path: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_a_fresh_store_is_stamped_with_the_current_version(tmp_path):
    db = str(tmp_path / "m.db")
    ObservationStore(db)
    assert _version(db) == SCHEMA_VERSION


def test_both_stores_share_one_file_without_fighting_over_the_stamp(tmp_path):
    # They open the same path with separate connections and both migrate.
    db = str(tmp_path / "m.db")
    ObservationStore(db)
    ConstraintStore(db)
    assert _version(db) == SCHEMA_VERSION


def test_a_preversioning_store_keeps_its_data_and_gains_a_stamp(tmp_path):
    """A store written before versioning has user_version 0 and real data.

    It must be stamped in place. Treating version 0 as "empty" would be the
    same class of error as CREATE TABLE IF NOT EXISTS: silently doing nothing
    to a database that needed attention.
    """
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE observations (
            uid TEXT PRIMARY KEY, text TEXT NOT NULL, channel TEXT NOT NULL,
            provenance TEXT NOT NULL, session_id TEXT,
            observed_at TEXT NOT NULL, anchors TEXT NOT NULL
        );
        CREATE TABLE constraints (
            uid TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
            necessity TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,
            source TEXT NOT NULL, frame_slot TEXT, tier TEXT NOT NULL,
            applicability TEXT NOT NULL, created_at TEXT NOT NULL,
            decay_class TEXT NOT NULL, last_observed_at TEXT NOT NULL
        );
        CREATE TABLE constraint_observations (
            constraint_uid TEXT NOT NULL, observation_uid TEXT NOT NULL,
            PRIMARY KEY (constraint_uid, observation_uid)
        );
        """
    )
    conn.execute(
        "INSERT INTO observations VALUES ('u1','oats before gym','planning',"
        "'observed',NULL,'2026-08-01T09:00:00+00:00','[]')"
    )
    conn.commit()
    assert _version(db) == 0
    conn.close()

    store = ObservationStore(db)
    assert _version(db) == SCHEMA_VERSION
    # The pre-existing row survived; baselining is not a rebuild.
    assert len(store.all()) == 1


def test_a_store_missing_a_column_fails_loudly_not_as_an_index_error(tmp_path):
    """The exact #155 failure: stamped current, but shaped older.

    Before this, the first read raised IndexError from inside a row mapper,
    which reads as a bug in the mapper rather than a database that needs
    migrating.
    """
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    # constraints without decay_class or last_observed_at — the shape before
    # #152 shipped.
    conn.executescript(
        """
        CREATE TABLE observations (
            uid TEXT PRIMARY KEY, text TEXT NOT NULL, channel TEXT NOT NULL,
            provenance TEXT NOT NULL, session_id TEXT,
            observed_at TEXT NOT NULL, anchors TEXT NOT NULL
        );
        CREATE TABLE constraints (
            uid TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
            necessity TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL,
            source TEXT NOT NULL, frame_slot TEXT, tier TEXT NOT NULL,
            applicability TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE constraint_observations (
            constraint_uid TEXT NOT NULL, observation_uid TEXT NOT NULL,
            PRIMARY KEY (constraint_uid, observation_uid)
        );
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(SchemaMismatch) as excinfo:
        ConstraintStore(db)
    message = str(excinfo.value)
    assert "decay_class" in message
    assert "last_observed_at" in message


def test_a_database_from_a_newer_build_is_refused(tmp_path):
    """Opening it read-write would drop columns this build cannot see."""
    db = str(tmp_path / "future.db")
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 5}")
    conn.commit()
    with pytest.raises(SchemaTooNew):
        apply_migrations(conn)
    conn.close()


def test_migrating_twice_is_a_no_op(tmp_path):
    db = str(tmp_path / "m.db")
    ObservationStore(db)
    conn = sqlite3.connect(db)
    assert apply_migrations(conn) == SCHEMA_VERSION
    assert apply_migrations(conn) == SCHEMA_VERSION
    conn.close()


def test_version_four_adds_the_kind_registry_and_the_required_block_link(tmp_path):
    db = str(tmp_path / "m.db")
    ObservationStore(db)
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"enforceable_kinds", "constraint_required_blocks"} <= tables
        kind_cols = {r[1] for r in conn.execute("PRAGMA table_info(enforceable_kinds)")}
        assert kind_cols == {"slug", "anchor_uid", "rule_observation_uid", "created_at"}
        link_cols = {r[1] for r in conn.execute("PRAGMA table_info(constraint_required_blocks)")}
        assert link_cols == {"constraint_uid", "slug"}
    finally:
        conn.close()
    assert _version(db) == 4


def test_a_version_three_store_upgrades_to_four_and_keeps_its_rows(tmp_path):
    """The case nothing exercised before #155: a store older than the code."""
    db = str(tmp_path / "m.db")
    conn = sqlite3.connect(db)
    try:
        from memory.migrations import _MIGRATIONS

        for v in (1, 2, 3):
            conn.executescript(_MIGRATIONS[v])
        conn.execute("PRAGMA user_version = 3")
        conn.execute(
            "INSERT INTO constraints VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("c1", "n", "d", "must", "profile", "proposed", "user", None, "durable",
             "{}", "2026-01-01T00:00:00+00:00", "permanent", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    store = ConstraintStore(db)
    assert _version(db) == 4
    assert [c.uid for c in store.all()] == ["c1"]
