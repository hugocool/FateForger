"""The fixture is three locked days and one request, and its store is pinned by
hash. Everything a spike reads is asserted here offline."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.stage1.days import FIXTURE_DAYS, FIXTURE_STORE_SHA256, snapshot_for


def test_three_days_with_the_same_request() -> None:
    assert [d.key for d in FIXTURE_DAYS] == ["working_tuesday", "vacation_day", "sunday"]
    assert {d.request for d in FIXTURE_DAYS} == {"deep work in the morning, gym at 18:00"}
    assert [d.date.strftime("%A") for d in FIXTURE_DAYS] == ["Tuesday", "Wednesday", "Sunday"]


def test_a_fixture_snapshot_is_locked_and_carries_its_rows() -> None:
    rows = [{"uid": "c1", "name": "x", "necessity": "must", "anchors": []}]
    snapshot = snapshot_for(FIXTURE_DAYS[0], rows)
    assert snapshot.planning_day is not None
    assert snapshot.applicable_constraints == rows
    assert snapshot.stage1 == "open"


def test_the_pinned_store_hash_is_a_full_sha256() -> None:
    """A truncated or placeholder pin would make verify_store refuse everything."""
    assert len(FIXTURE_STORE_SHA256) == 64
    assert bytes.fromhex(FIXTURE_STORE_SHA256)


def test_every_fixture_day_has_golden_facts() -> None:
    from tests.fixtures.stage1.days import FIXTURE_DAYS, load_golden

    golden = load_golden()
    assert set(golden) == {d.key for d in FIXTURE_DAYS}
    assert len(golden["working_tuesday"]) >= 6
    assert all(isinstance(line, str) for facts in golden.values() for line in facts)


def _write_golden(tmp_path, tuesday_facts: str) -> Path:
    path = tmp_path / "golden.toml"
    path.write_text(
        f"[working_tuesday]\nfacts = {tuesday_facts}\n\n"
        '[vacation_day]\nfacts = ["a"]\n\n'
        '[sunday]\nfacts = ["b"]\n\n'
        '[notes]\nfacts = ["not a fixture day"]\n'
    )
    return path


def test_a_non_string_golden_fact_is_refused(tmp_path) -> None:
    """Silently str()-ing a number would put a fact in the script nobody wrote."""
    from tests.fixtures.stage1.days import load_golden

    with pytest.raises(ValueError):
        load_golden(_write_golden(tmp_path, '["a", 3]'))


def test_golden_returns_only_the_fixture_days(tmp_path) -> None:
    from tests.fixtures.stage1.days import FIXTURE_DAYS, load_golden

    golden = load_golden(_write_golden(tmp_path, '["a"]'))
    assert set(golden) == {d.key for d in FIXTURE_DAYS}
