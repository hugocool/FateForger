"""The fixture is three locked days and one request, and its store is pinned by
hash. Everything a spike reads is asserted here offline."""
from __future__ import annotations

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
