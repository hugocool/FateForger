# tests/memory/test_split.py
"""Splitting a constraint that should never have been one (#169 follow-on).

The store could merge and could not un-merge. Undoing the merge that reached
Hugo's real corpus — "Lunch: Lunch break" folded into "include breakfast,
lunch and dinner every day" — required deleting a row from
`constraint_observations` with raw SQL, because the API had no verb for it.
The one corrective operation the store most needed had to be performed
underneath the store.

The asymmetry is exact: L1 is append-only and keeps every observation, so the
evidence to split was always present. What was missing is that the *partition*
of observations into constraints is derived state that nothing re-derives.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memory.anchor_store import AnchorStore
from memory.constraint import (
    Applicability,
    Constraint,
    Necessity,
    Scope,
    Source,
    Status,
)
from memory.constraint_store import ConstraintStore
from memory.judge import StubJudge
from memory.models import Channel, DecayClass, Observation, Provenance, Tier
from memory.reprojection import split
from memory.store import ObservationStore

WHEN = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
MEALS = "Daily Meals: include breakfast, lunch and dinner every day"
LUNCH = "Lunch: lunch break"


def _stores(tmp_path):
    db = str(tmp_path / "m.db")
    return ObservationStore(db), ConstraintStore(db), AnchorStore(db)


def _observe(store, text, at=WHEN):
    observation = Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        observed_at=at,
    )
    store.append(observation)
    return observation


def _merged(constraints, first, second):
    """A constraint as the pre-#169 canonicalise judge produced it."""
    constraint = Constraint(
        name="Daily Meals",
        description=first.text,
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[first.uid, second.uid],
        created_at=first.observed_at,
        decay_class=DecayClass.PERMANENT,
        last_observed_at=second.observed_at,
    )
    constraints.upsert(constraint)
    return constraint


def _judge():
    return StubJudge(tiers={MEALS: Tier.DURABLE, LUNCH: Tier.DURABLE})


async def test_the_real_merge_can_be_undone(tmp_path):
    observations, constraints, _ = _stores(tmp_path)
    meals = _observe(observations, MEALS)
    lunch = _observe(observations, LUNCH, at=WHEN + timedelta(days=6))
    merged = _merged(constraints, meals, lunch)

    original, newborn = await split(
        observations,
        constraints,
        _judge(),
        uid=merged.uid,
        observation_uids=[lunch.uid],
    )

    assert original == merged.uid                      # identity survives (I3)
    assert newborn != merged.uid
    assert constraints.observations_for(original) == [meals.uid]
    assert constraints.observations_for(newborn) == [lunch.uid]
    assert constraints.get(original).description == MEALS
    assert constraints.get(newborn).description == LUNCH
    assert len(constraints.all()) == 2


async def test_both_halves_are_reprojected_from_what_they_now_hold(tmp_path):
    """Not a link move: each side's derived fields must describe its own
    evidence, or the halves keep describing the merge they came from."""
    observations, constraints, _ = _stores(tmp_path)
    meals = _observe(observations, MEALS)
    lunch = _observe(observations, LUNCH, at=WHEN + timedelta(days=6))
    merged = _merged(constraints, meals, lunch)

    _, newborn = await split(
        observations, constraints, _judge(),
        uid=merged.uid, observation_uids=[lunch.uid],
    )

    # last_observed_at on the original was the lunch observation's; after the
    # split it can only be the meals one.
    assert constraints.get(merged.uid).last_observed_at == meals.observed_at
    assert constraints.get(newborn).last_observed_at == lunch.observed_at
    assert constraints.get(newborn).created_at == lunch.observed_at


async def test_l1_is_untouched(tmp_path):
    """A split rearranges derived state. The log is append-only and the
    evidence for the split is what made it possible."""
    observations, constraints, _ = _stores(tmp_path)
    meals = _observe(observations, MEALS)
    lunch = _observe(observations, LUNCH, at=WHEN + timedelta(days=6))
    merged = _merged(constraints, meals, lunch)

    await split(observations, constraints, _judge(),
                uid=merged.uid, observation_uids=[lunch.uid])

    assert len(observations.all()) == 2
    assert observations.get(meals.uid).text == MEALS
    assert observations.get(lunch.uid).text == LUNCH


async def test_the_new_constraint_is_reachable_from_its_own_anchors(tmp_path):
    """A split constraint with no anchors is unreachable from any walk, which
    a caller cannot tell apart from a rule that does not apply today."""
    observations, constraints, anchors = _stores(tmp_path)
    meals = _observe(observations, MEALS)
    lunch = _observe(observations, LUNCH, at=WHEN + timedelta(days=6))
    meals.anchors, lunch.anchors = ["meals"], ["lunch"]
    for observation in (meals, lunch):
        observations._conn.execute(
            "UPDATE observations SET anchors = ? WHERE uid = ?",
            (f'["{observation.anchors[0]}"]', observation.uid),
        )
    observations._conn.commit()
    merged = _merged(constraints, meals, lunch)

    _, newborn = await split(
        observations, constraints, _judge(),
        uid=merged.uid, observation_uids=[lunch.uid], anchor_store=anchors,
    )

    by_name = {a.name: a.uid for a in anchors.all()}
    assert anchors.constraints_reachable_from([by_name["lunch"]]) == {newborn}
    assert anchors.constraints_reachable_from([by_name["meals"]]) == {merged.uid}


@pytest.mark.parametrize(
    "moving,expected",
    [
        ([], "no observations named"),
        (["deadbeef" * 4], "not provenance"),
    ],
)
async def test_a_split_that_would_corrupt_provenance_is_refused(
    tmp_path, moving, expected
):
    observations, constraints, _ = _stores(tmp_path)
    meals = _observe(observations, MEALS)
    lunch = _observe(observations, LUNCH, at=WHEN + timedelta(days=6))
    merged = _merged(constraints, meals, lunch)

    with pytest.raises(ValueError, match=expected):
        await split(observations, constraints, _judge(),
                    uid=merged.uid, observation_uids=moving)


async def test_moving_every_observation_is_refused(tmp_path):
    """That is a rename, and it leaves a constraint with no evidence that
    re-projection could never re-derive."""
    observations, constraints, _ = _stores(tmp_path)
    meals = _observe(observations, MEALS)
    lunch = _observe(observations, LUNCH, at=WHEN + timedelta(days=6))
    merged = _merged(constraints, meals, lunch)

    with pytest.raises(ValueError, match="every observation"):
        await split(observations, constraints, _judge(),
                    uid=merged.uid, observation_uids=[meals.uid, lunch.uid])


async def test_splitting_an_unknown_constraint_is_refused(tmp_path):
    observations, constraints, _ = _stores(tmp_path)
    with pytest.raises(ValueError, match="unknown constraint"):
        await split(observations, constraints, _judge(),
                    uid="deadbeef" * 4, observation_uids=["x"])
