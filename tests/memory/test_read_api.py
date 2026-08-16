# tests/memory/test_read_api.py
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint import (
    Applicability,
    Constraint,
    ConstraintView,
    Necessity,
    Scope,
    Source,
    Status,
)
from memory.constraint_store import ConstraintStore
from memory.models import Tier
from memory.read_api import get_active_constraints

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
MONDAY = date(2026, 3, 9)
SUNDAY = date(2026, 3, 8)


def _c(name: str, tier: Tier = Tier.DURABLE, **app) -> Constraint:
    return Constraint(
        name=name,
        description=f"description of {name}",
        necessity=Necessity.MUST,
        scope=Scope.PROFILE,
        status=Status.LOCKED,
        source=Source.USER,
        frame_slot=None,
        tier=tier,
        applicability=Applicability(**app),
        source_observation_uids=[],
        created_at=T0,
    )


def test_returns_durable_constraints_as_views(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Sleep 23:00"))
    result = get_active_constraints(store, MONDAY)
    assert len(result) == 1
    assert isinstance(result[0], ConstraintView)
    assert result[0].name == "Sleep 23:00"


def test_session_tier_constraints_are_excluded(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Hockey today", tier=Tier.SESSION))
    assert get_active_constraints(store, MONDAY) == []


def test_day_of_week_filtering_is_structural(tmp_path):
    """Comparing a weekday to a list of weekdays is arithmetic, not matching."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Client on Tue and Thu", days_of_week=[1, 3]))
    assert get_active_constraints(store, MONDAY) == []
    assert len(get_active_constraints(store, date(2026, 3, 10))) == 1


def test_date_range_filtering(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(
        _c("Sprint focus", start_date=date(2026, 3, 1), end_date=date(2026, 3, 5))
    )
    assert get_active_constraints(store, MONDAY) == []
    assert len(get_active_constraints(store, date(2026, 3, 3))) == 1


def test_a_constraint_with_no_applicability_applies_every_day(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Sleep 23:00"))
    assert len(get_active_constraints(store, MONDAY)) == 1
    assert len(get_active_constraints(store, SUNDAY)) == 1


def test_the_view_carries_the_uid_so_the_journal_can_join(tmp_path):
    """Map A's journal emits `unresolvable` without this."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c("Sleep 23:00")
    store.upsert(c)
    assert get_active_constraints(store, MONDAY)[0].uid == c.uid


def test_the_read_path_cannot_reach_a_model():
    """I1: no model call in the read path. Enforced, not merely conventional."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "memory" / "read_api.py"
    ).read_text()
    for forbidden in ("judge", "openrouter", "httpx", "await"):
        assert forbidden not in source, f"read_api.py must not reference {forbidden}"
