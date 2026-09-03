"""Active constraints arrive boundaries-first, most recently stated next.

Until 2026-08-31 `get_active_constraints` returned rows in store order, which
is `ORDER BY created_at` — so a hard boundary and a mild preference were
interleaved by the accident of when each was first written, and a rule stated
this morning sat behind one from months ago.

Nothing was dropped; every applicable rule is still returned. What was wrong is
that the reader met them in an order carrying no meaning, and that a cap over
an unordered list would have kept whatever happened to be created first.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from memory.constraint import Constraint, Necessity, Scope, Source, Status
from memory.constraint_store import ConstraintStore
from memory.models import Tier
from memory.read_api import get_active_constraints

DAY = date(2026, 8, 31)
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)


def _c(name: str, necessity: Necessity, *, observed_days_ago: int) -> Constraint:
    seen = NOW - timedelta(days=observed_days_ago)
    return Constraint(
        name=name,
        description=f"{name} description",
        necessity=necessity,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        created_at=NOW - timedelta(days=400),
        last_observed_at=seen,
    )


def _store(tmp_path, *constraints) -> ConstraintStore:
    store = ConstraintStore(str(tmp_path / "memory.db"))
    for c in constraints:
        store.upsert(c)
    return store


def test_boundaries_come_before_preferences(tmp_path):
    """A MUST the user has not restated for a year still outranks a fresh SHOULD.

    Necessity must dominate recency. Inverting them lets enthusiasm outrank a
    rule the user cannot break.
    """
    store = _store(
        tmp_path,
        _c("fresh preference", Necessity.SHOULD, observed_days_ago=0),
        _c("old boundary", Necessity.MUST, observed_days_ago=300),
    )
    names = [v.name for v in get_active_constraints(store, DAY)]
    assert names.index("old boundary") < names.index("fresh preference")


def test_within_one_necessity_the_most_recent_comes_first(tmp_path):
    """What he said this morning should not queue behind last spring."""
    store = _store(
        tmp_path,
        _c("stated in spring", Necessity.MUST, observed_days_ago=180),
        _c("stated this morning", Necessity.MUST, observed_days_ago=0),
        _c("stated last week", Necessity.MUST, observed_days_ago=7),
    )
    names = [v.name for v in get_active_constraints(store, DAY)]
    assert names == ["stated this morning", "stated last week", "stated in spring"]


def test_order_does_not_follow_creation_order(tmp_path):
    """The defect this replaces, pinned directly.

    Both rows are created in an order that would put the preference first if
    the store's own `ORDER BY created_at` still decided the result.
    """
    older_created = _c("preference", Necessity.SHOULD, observed_days_ago=0)
    boundary = _c("boundary", Necessity.MUST, observed_days_ago=0)
    boundary.created_at = older_created.created_at + timedelta(days=1)
    store = _store(tmp_path, older_created, boundary)
    assert [v.name for v in get_active_constraints(store, DAY)][0] == "boundary"


def test_nothing_is_dropped_by_ordering(tmp_path):
    """Ordering is not filtering. Every applicable rule still comes back."""
    store = _store(
        tmp_path,
        _c("a", Necessity.MUST, observed_days_ago=1),
        _c("b", Necessity.SHOULD, observed_days_ago=2),
        _c("c", Necessity.MUST, observed_days_ago=3),
    )
    assert {v.name for v in get_active_constraints(store, DAY)} == {"a", "b", "c"}


def test_an_empty_store_still_answers(tmp_path):
    assert get_active_constraints(_store(tmp_path), DAY) == []
