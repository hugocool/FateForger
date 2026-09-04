"""Builds the shared fixture against a copy of a real store. Marked slow.

Set STAGE1_FIXTURE_DB to a memory.db to copy; without it the tests skip with
that reason. The live data/memory.db is never opened in place.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.fixtures.stage1.days import FIXTURE_DAYS, load_labels, rows_for, snapshot_for

pytestmark = pytest.mark.slow

LABELS = Path(__file__).resolve().parents[1] / "fixtures" / "stage1" / "labels.toml"


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


@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=[d.key for d in FIXTURE_DAYS])
def test_every_day_has_hand_labels_before_a_spike_may_run(day) -> None:
    labels = load_labels(LABELS)
    if not labels.get(day.key):
        pytest.fail(f"{day.key} has no hand-labelled gaps; a spike against it measures nothing")
