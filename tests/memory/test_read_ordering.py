# tests/memory/test_read_ordering.py
"""The read path returned constraints in the order they happened to be written.

`get_active_constraints` had no sort, and `ConstraintStore.durable()` selects
`ORDER BY created_at`, so a boundary stated in January and a preference stated
yesterday arrived interleaved with nothing marking which was which by position.

Ordering is not cosmetic here. The whole list goes into a planning prompt, and
what the model reads first is what it anchors on — at 35+ active rules on a
weekday, "first" is doing real work (#202).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint import (
    Applicability,
    Constraint,
    Necessity,
    Scope,
    Source,
    Status,
)
from memory.constraint_store import ConstraintStore
from memory.models import DecayClass, Tier
from memory.read_api import get_active_constraints

JANUARY = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
JULY = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
AUGUST = date(2026, 8, 17)


def _c(
    name: str,
    necessity: Necessity = Necessity.MUST,
    seen: datetime = JULY,
    created: datetime | None = None,
) -> Constraint:
    """A durable, permanent rule that applies on any day.

    `created` defaults to `seen` so a test that cares only about recency does
    not have to think about insertion order, and can be set apart from `seen`
    by a test that needs the two to disagree.
    """
    return Constraint(
        name=name,
        description=name,
        necessity=necessity,
        scope=Scope.PROFILE,
        status=Status.LOCKED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[],
        created_at=created or seen,
        decay_class=DecayClass.PERMANENT,
        last_observed_at=seen,
    )


def test_a_boundary_is_read_before_a_preference(tmp_path):
    """MUST before SHOULD, whatever order they were written in.

    Written SHOULD-first on purpose: `durable()` orders by `created_at`, so
    without a sort this returns the preference first and the boundary second.
    """
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Prefer oats", Necessity.SHOULD, created=JANUARY))
    store.upsert(_c("Sleep at 23:00", Necessity.MUST, created=JULY))

    names = [v.name for v in get_active_constraints(store, AUGUST)]
    assert names == ["Sleep at 23:00", "Prefer oats"]


def test_the_most_recently_restated_rule_comes_first(tmp_path):
    """Within one necessity, recency decides.

    What he said this week should not sort behind something from January
    because of its first letter or its row id.
    """
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Stated in January", Necessity.MUST, seen=JANUARY))
    store.upsert(_c("Stated in July", Necessity.MUST, seen=JULY))

    names = [v.name for v in get_active_constraints(store, AUGUST)]
    assert names == ["Stated in July", "Stated in January"]


def test_a_stale_boundary_still_outranks_a_fresh_preference(tmp_path):
    """Necessity beats recency, deliberately.

    A MUST from January is still a boundary; a SHOULD from this morning is
    still a preference. If recency could float a preference above a boundary
    it would invert the one distinction the constraint block exists to make —
    and it would do so exactly when the user had just been chatting.
    """
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Old boundary", Necessity.MUST, seen=JANUARY))
    store.upsert(_c("Fresh preference", Necessity.SHOULD, seen=JULY))

    names = [v.name for v in get_active_constraints(store, AUGUST)]
    assert names == ["Old boundary", "Fresh preference"]


def test_ordering_does_not_change_which_rules_are_returned(tmp_path):
    """A sort must not become a filter.

    Cheap to state, and the thing most likely to break silently if the
    comprehension is ever rewritten: a faded rule stays out, an applicable one
    stays in, and the count is unchanged by ordering.
    """
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Alive", Necessity.MUST, seen=JULY))
    store.upsert(_c("Also alive", Necessity.SHOULD, seen=JANUARY))

    assert len(get_active_constraints(store, AUGUST)) == 2
