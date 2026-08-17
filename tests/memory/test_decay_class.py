from __future__ import annotations

from memory.models import HALF_LIFE_DAYS, DecayClass


def test_the_four_seed_classes_exist():
    assert {c.value for c in DecayClass} == {
        "permanent",
        "seasonal",
        "project",
        "daily",
    }


def test_permanent_never_fades():
    """A sleep window must not expire because it went unmentioned."""
    assert HALF_LIFE_DAYS[DecayClass.PERMANENT] is None


def test_half_lives_are_ordered_and_finite_for_the_rest():
    assert HALF_LIFE_DAYS[DecayClass.DAILY] < HALF_LIFE_DAYS[DecayClass.PROJECT]
    assert HALF_LIFE_DAYS[DecayClass.PROJECT] < HALF_LIFE_DAYS[DecayClass.SEASONAL]
    for c in (DecayClass.DAILY, DecayClass.PROJECT, DecayClass.SEASONAL):
        assert HALF_LIFE_DAYS[c] > 0


def test_every_class_has_a_half_life():
    """A class with no entry would raise at read time on a live query."""
    assert set(HALF_LIFE_DAYS) == set(DecayClass)
