"""How close a rule is to fading, as a number in [0, 1] the server computes.

The half-life table stays inside the memory server; a host that sorted by
`last_observed_at` would have to know it. `None` means the rule never fades.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from memory.constraint import Constraint, Necessity, Scope, Source, Status
from memory.constraint_store import ConstraintStore
from memory.models import HALF_LIFE_DAYS, DecayClass, Tier
from memory.read_api import fade_on, get_active_constraints

DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc)


def _rule(name: str, decay_class: DecayClass, *, observed_days_ago: int) -> Constraint:
    return Constraint(
        name=name,
        description=f"{name} description",
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        decay_class=decay_class,
        created_at=NOW - timedelta(days=400),
        last_observed_at=NOW - timedelta(days=observed_days_ago),
    )


def _a_decaying_class() -> DecayClass:
    return next(cls for cls, half in HALF_LIFE_DAYS.items() if half is not None)


def test_a_permanent_rule_never_fades() -> None:
    assert fade_on(_rule("Sleep", DecayClass.PERMANENT, observed_days_ago=900), DAY) is None


def test_fade_is_elapsed_over_half_life_clipped_to_one() -> None:
    cls = _a_decaying_class()
    half = HALF_LIFE_DAYS[cls]
    fresh = _rule("Fresh", cls, observed_days_ago=0)
    halfway = _rule("Halfway", cls, observed_days_ago=half // 2)
    stale = _rule("Stale", cls, observed_days_ago=half * 3)
    assert fade_on(fresh, DAY) == 0.0
    assert abs(fade_on(halfway, DAY) - (half // 2) / half) < 1e-9
    assert fade_on(stale, DAY) == 1.0


def test_active_views_carry_fade(tmp_path) -> None:
    cls = _a_decaying_class()
    store = ConstraintStore(str(tmp_path / "memory.db"))
    store.upsert(_rule("Halfway", cls, observed_days_ago=HALF_LIFE_DAYS[cls] // 2))
    store.upsert(_rule("Sleep", DecayClass.PERMANENT, observed_days_ago=5))

    views = {v.name: v for v in get_active_constraints(store, DAY)}

    assert views["Sleep"].fade is None
    assert 0.0 < views["Halfway"].fade < 1.0
