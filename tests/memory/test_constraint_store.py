# tests/memory/test_constraint_store.py
from __future__ import annotations

from datetime import datetime, timezone

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

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _c(name: str = "Oats before gym", **kw) -> Constraint:
    defaults = dict(
        name=name,
        description="Eat oats two hours before gym",
        necessity=Necessity.MUST,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        frame_slot="pre_gym_meal",
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=["obs-1"],
        created_at=T0,
    )
    defaults.update(kw)
    return Constraint(**defaults)


def test_a_constraint_gets_a_minted_uid(tmp_path):
    """The timebox journal is blocked on this existing."""
    a, b = _c(), _c()
    assert a.uid and b.uid
    assert a.uid != b.uid, "identity must not be derived from content"


def test_upsert_and_get_round_trip(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    uid = store.upsert(c)
    got = store.get(uid)
    assert got is not None
    assert got.name == "Oats before gym"
    assert got.frame_slot == "pre_gym_meal"
    assert got.tier is Tier.DURABLE
    assert got.source_observation_uids == ["obs-1"]


def test_upsert_by_uid_replaces_and_does_not_duplicate(tmp_path):
    """A constraint is derived, so re-projection must not accumulate copies."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    store.upsert(c)
    c.description = "Eat oats 2h before any sport"
    c.source_observation_uids = ["obs-1", "obs-2"]
    store.upsert(c)
    assert len(store.all()) == 1
    assert store.get(c.uid).description == "Eat oats 2h before any sport"


def test_durable_filters_by_tier(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Oats before gym", tier=Tier.DURABLE))
    store.upsert(_c("Hockey at 11:45 today", tier=Tier.SESSION))
    durable = store.durable()
    assert len(durable) == 1
    assert durable[0].name == "Oats before gym"


def test_to_view_carries_the_seven_fields_the_patcher_needs(tmp_path):
    view = _c().to_view()
    assert isinstance(view, ConstraintView)
    assert view.name == "Oats before gym"
    assert view.necessity == "must"
    assert view.scope == "profile"
    assert view.status == "proposed"
    assert view.source == "user"
    assert view.description == "Eat oats two hours before gym"
    assert view.frame_slot == "pre_gym_meal"


def test_applicability_defaults_to_always(tmp_path):
    a = Applicability()
    assert a.start_date is None
    assert a.end_date is None
    assert a.days_of_week == []


def test_linking_the_same_observation_twice_is_idempotent(tmp_path):
    """Append must not replay a prior payload; the link is a set."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    store.upsert(c)
    store.link_observation(c.uid, "obs-2")
    store.link_observation(c.uid, "obs-2")
    assert store.observations_for(c.uid) == ["obs-1", "obs-2"]
    assert store.get(c.uid).source_observation_uids == ["obs-1", "obs-2"]


def test_replace_links_drops_links_that_are_no_longer_present(tmp_path):
    """Re-projection can remove an observation, not only add one."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    store.upsert(c)
    store.link_observation(c.uid, "obs-2")
    assert store.observations_for(c.uid) == ["obs-1", "obs-2"]
    store.replace_links(c.uid, ["obs-2", "obs-3"])
    assert store.observations_for(c.uid) == ["obs-2", "obs-3"]


def test_replace_links_is_idempotent(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    store.upsert(c)
    store.replace_links(c.uid, ["obs-9"])
    store.replace_links(c.uid, ["obs-9"])
    assert store.observations_for(c.uid) == ["obs-9"]


def test_replace_links_with_an_empty_list_clears_provenance(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    store.upsert(c)
    store.replace_links(c.uid, [])
    assert store.observations_for(c.uid) == []


def test_replace_links_does_not_touch_another_constraints_links(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    a, b = _c("A"), _c("B")
    store.upsert(a)
    store.upsert(b)
    store.replace_links(a.uid, ["obs-x"])
    assert store.observations_for(b.uid) == ["obs-1"]


def test_reprojection_that_drops_an_observation_drops_its_link(tmp_path):
    """I4: re-projection must be able to shrink provenance, not only grow it."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c()
    store.upsert(c)
    store.link_observation(c.uid, "obs-2")
    assert store.observations_for(c.uid) == ["obs-1", "obs-2"]

    c.source_observation_uids = ["obs-1"]
    store.upsert(c)
    assert store.observations_for(c.uid) == ["obs-1"]
    assert store.get(c.uid).source_observation_uids == ["obs-1"]


def test_applicability_boundaries_are_inclusive():
    from datetime import date as _date

    a = Applicability(start_date=_date(2026, 3, 1), end_date=_date(2026, 3, 5))
    assert a.applies_on(_date(2026, 3, 1)) is True
    assert a.applies_on(_date(2026, 3, 5)) is True
    assert a.applies_on(_date(2026, 2, 28)) is False
    assert a.applies_on(_date(2026, 3, 6)) is False


def test_weekday_indices_outside_zero_to_six_are_rejected():
    """ISO numbering (Sunday=7) would match no real date and hide the rule."""
    import pytest

    with pytest.raises(ValueError, match="out of range"):
        Applicability(days_of_week=[7])
    with pytest.raises(ValueError, match="out of range"):
        Applicability(days_of_week=[-1])


def test_the_full_valid_week_is_accepted():
    a = Applicability(days_of_week=[0, 1, 2, 3, 4, 5, 6])
    assert len(a.days_of_week) == 7
