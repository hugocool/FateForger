"""Builds the shared fixture against a copy of a real store. Marked slow.

Set STAGE1_FIXTURE_DB to a memory.db to copy; without it the tests skip with
that reason. The live data/memory.db is never opened in place.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.fixtures.stage1.days import FIXTURE_DAYS, rows_for, snapshot_for

pytestmark = pytest.mark.slow


@pytest.fixture
def store_copy(tmp_path) -> str:
    source = os.environ.get("STAGE1_FIXTURE_DB", "").strip()
    if not source:
        pytest.skip("STAGE1_FIXTURE_DB not set; the fixture needs a store to copy")
    target = tmp_path / "memory.db"
    shutil.copy(source, target)
    return str(target)


@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=[d.key for d in FIXTURE_DAYS])
def test_each_day_yields_rows_and_a_locked_snapshot(store_copy, day) -> None:
    rows = rows_for(store_copy, day)
    snapshot = snapshot_for(day, rows)
    assert snapshot.applicable_constraints == rows
    assert all("anchors" in row for row in rows)


def test_a_store_whose_bytes_moved_is_refused(store_copy, tmp_path) -> None:
    """The evals measure against one frozen store. A copy that differs -- a
    relink, a migration, a new rule -- silently changes every matrix, so the
    hash is pinned and a mismatch fails here, not in a number nobody trusts."""
    from tests.fixtures.stage1.days import verify_store

    verify_store(store_copy)
    drifted = tmp_path / "drifted.db"
    drifted.write_bytes(Path(store_copy).read_bytes() + b"\x00")
    with pytest.raises(RuntimeError, match="sha256"):
        verify_store(str(drifted))
