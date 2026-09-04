"""Whether a rule holds every day, on some days, or inside a dated window.

Three words the card can tag a row with, decided arithmetically from the
stored applicability so no date or day type leaves the server.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint import Applicability, Constraint, Necessity, Scope, Source, Status
from memory.constraint_store import ConstraintStore
from memory.models import Tier
from memory.read_api import applies_of, get_active_constraints

DAY = date(2026, 9, 8)  # a Tuesday
NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _rule(name: str, applicability: Applicability) -> Constraint:
    return Constraint(
        name=name,
        description=f"{name} description",
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=applicability,
        created_at=NOW,
        last_observed_at=NOW,
    )


def test_dated_wins_over_weekdays_and_day_types() -> None:
    assert applies_of(Applicability(start_date=date(2026, 9, 1), days_of_week=[1])) == "dated"
    assert applies_of(Applicability(end_date=date(2026, 9, 30), day_types=["working"])) == "dated"


def test_weekdays_or_day_types_are_some_days() -> None:
    assert applies_of(Applicability(days_of_week=[1, 3])) == "some_days"
    assert applies_of(Applicability(day_types=["working"])) == "some_days"


def test_nothing_set_is_every_day() -> None:
    assert applies_of(Applicability()) == "every_day"


def test_active_views_carry_applies(tmp_path) -> None:
    store = ConstraintStore(str(tmp_path / "memory.db"))
    store.upsert(_rule("Sleep", Applicability()))
    store.upsert(_rule("Commute", Applicability(day_types=["working"])))
    store.upsert(_rule("Trip", Applicability(start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))))

    views = {v.name: v.applies for v in get_active_constraints(store, DAY, day_type="working")}

    assert views == {"Sleep": "every_day", "Commute": "some_days", "Trip": "dated"}
