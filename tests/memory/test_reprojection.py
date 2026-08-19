# tests/memory/test_reprojection.py
"""Re-projection: L2 re-derived from L1 (#154, invariant I4).

The gap these cover: project() wrote derived fields only on its create branch
and a fold touched nothing but last_observed_at, so a judgement improvement
reached only constraints created after it shipped. Every test here starts from
a store OLDER than the judge, which is the case that had never been exercised
because every run re-seeded from scratch.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

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
from memory.reprojection import reproject
from memory.store import ObservationStore

WHEN = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


def _stores(tmp_path):
    db = str(tmp_path / "m.db")
    return ObservationStore(db), ConstraintStore(db)


def _observe(store, text, *, at=WHEN, channel=Channel.PLANNING):
    observation = Observation(
        text=text,
        channel=channel,
        provenance=Provenance.OBSERVED,
        observed_at=at,
    )
    store.append(observation)
    return observation


def _stale_constraint(constraint_store, observation, **overrides):
    """A constraint as an older build would have written it."""
    fields = dict(
        name=observation.text,
        description=observation.text,
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[observation.uid],
        created_at=observation.observed_at,
        decay_class=DecayClass.PERMANENT,
        last_observed_at=observation.observed_at,
    )
    fields.update(overrides)
    constraint = Constraint(**fields)
    constraint_store.upsert(constraint)
    return constraint


async def test_an_existing_constraint_acquires_applicability(tmp_path):
    """The exact case #154 was opened on.

    A Tue/Thu rule stored before applicability extraction shipped was still
    served on a Monday, and re-observing it could not fix that because the
    fold branch never wrote the field.
    """
    observations, constraints = _stores(tmp_path)
    observation = _observe(observations, "physio on tuesdays and thursdays")
    stale = _stale_constraint(constraints, observation)
    assert stale.applicability.days_of_week == []

    judge = StubJudge(
        tiers={"physio on tuesdays and thursdays": Tier.DURABLE},
        days_of_week={"physio on tuesdays and thursdays": [1, 3]},
    )
    report = await reproject(observations, constraints, judge)

    assert report.examined == 1
    assert len(report.changed) == 1
    assert "applicability" in report.changed[0].fields
    assert constraints.get(stale.uid).applicability.days_of_week == [1, 3]


async def test_identity_survives_reprojection(tmp_path):
    """I3: the uid is minted once. Anything holding a reference must still
    resolve after the fields underneath it are re-derived."""
    observations, constraints = _stores(tmp_path)
    observation = _observe(observations, "gym at six")
    stale = _stale_constraint(constraints, observation)

    judge = StubJudge(
        tiers={"gym at six": Tier.DURABLE},
        days_of_week={"gym at six": [0, 2, 4]},
    )
    await reproject(observations, constraints, judge)

    assert constraints.get(stale.uid) is not None
    assert len(constraints.all()) == 1


async def test_reprojection_is_idempotent(tmp_path):
    """Safe to run repeatedly, and must not multiply constraints."""
    observations, constraints = _stores(tmp_path)
    observation = _observe(observations, "no meetings before ten")
    _stale_constraint(constraints, observation)
    judge = StubJudge(
        tiers={"no meetings before ten": Tier.DURABLE},
        days_of_week={"no meetings before ten": [0, 1, 2, 3, 4]},
    )

    first = await reproject(observations, constraints, judge)
    second = await reproject(observations, constraints, judge)

    assert len(first.changed) == 1
    assert second.changed == []
    assert second.unchanged == 1
    assert len(constraints.all()) == 1


async def test_provenance_survives(tmp_path):
    """upsert calls replace_links, so re-projection is its first real caller
    and a wrong uid list here would silently erase the evidence."""
    observations, constraints = _stores(tmp_path)
    first = _observe(observations, "deep work in the morning")
    second = _observe(
        observations, "deep work in the morning", at=WHEN + timedelta(days=3)
    )
    stale = _stale_constraint(constraints, first)
    constraints.link_observation(stale.uid, second.uid)

    judge = StubJudge(tiers={"deep work in the morning": Tier.DURABLE})
    await reproject(observations, constraints, judge)

    assert set(constraints.observations_for(stale.uid)) == {
        first.uid,
        second.uid,
    }


async def test_derivation_uses_the_whole_evidence_set_not_the_first(tmp_path):
    """The core of the fix.

    A fold added an observation and updated one timestamp; the constraint kept
    the first observation's wording, tier and lifetime forever. Re-projection
    reads all of them: earliest sets created_at, latest sets the text and the
    lifetime, and one durable statement makes the rule durable.
    """
    observations, constraints = _stores(tmp_path)
    early = _observe(observations, "lunch around noon")
    late = _observe(
        observations, "lunch at 12:30 sharp", at=WHEN + timedelta(days=10)
    )
    stale = _stale_constraint(
        constraints, early, tier=Tier.SESSION, scope=Scope.SESSION
    )
    constraints.link_observation(stale.uid, late.uid)

    judge = StubJudge(
        tiers={"lunch at 12:30 sharp": Tier.DURABLE},  # early stays SESSION
        labels={"lunch at 12:30 sharp": "Lunch"},
        bindings={"lunch at 12:30 sharp": True},
        decay_classes={"lunch at 12:30 sharp": DecayClass.SEASONAL},
    )
    await reproject(observations, constraints, judge)

    result = constraints.get(stale.uid)
    assert result.description == "lunch at 12:30 sharp"   # newest text
    assert result.name == "Lunch"                          # newest label
    assert result.created_at == early.observed_at          # earliest evidence
    assert result.last_observed_at == late.observed_at     # latest evidence
    assert result.tier is Tier.DURABLE                     # tier moves up
    assert result.scope is Scope.PROFILE
    assert result.necessity is Necessity.MUST
    assert result.decay_class is DecayClass.SEASONAL


async def test_a_later_restatement_does_not_widen_a_scoped_rule(tmp_path):
    """A restatement omitting the scoping words must not silently make a
    Tue/Thu rule apply every day."""
    observations, constraints = _stores(tmp_path)
    scoped = _observe(observations, "physio tuesdays and thursdays")
    bare = _observe(observations, "physio matters", at=WHEN + timedelta(days=5))
    stale = _stale_constraint(constraints, scoped)
    constraints.link_observation(stale.uid, bare.uid)

    judge = StubJudge(
        tiers={"physio tuesdays and thursdays": Tier.DURABLE,
               "physio matters": Tier.DURABLE},
        days_of_week={"physio tuesdays and thursdays": [1, 3]},  # bare: none
    )
    await reproject(observations, constraints, judge)

    assert constraints.get(stale.uid).applicability.days_of_week == [1, 3]


async def test_reprojection_never_re_merges(tmp_path):
    """Re-deciding which constraints are the same rule belongs to #145.

    A re-projection that quietly merged rows would reshape the store on every
    run. The judge here raises if canonicalise is called at all.
    """
    observations, constraints = _stores(tmp_path)

    class NoCanonicalise(StubJudge):
        async def canonicalise(self, observation, candidates):
            raise AssertionError("re-projection must not re-run canonicalise")

    first = _observe(observations, "oats before gym")
    second = _observe(observations, "oats two hours before gym")
    _stale_constraint(constraints, first)
    _stale_constraint(constraints, second)

    judge = NoCanonicalise(
        tiers={"oats before gym": Tier.DURABLE,
               "oats two hours before gym": Tier.DURABLE}
    )
    await reproject(observations, constraints, judge)

    assert len(constraints.all()) == 2


async def test_a_constraint_with_no_provenance_is_skipped_not_guessed(tmp_path):
    observations, constraints = _stores(tmp_path)
    orphan = Constraint(
        name="from nowhere",
        description="from nowhere",
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[],
        created_at=WHEN,
        decay_class=DecayClass.PERMANENT,
        last_observed_at=WHEN,
    )
    constraints.upsert(orphan)

    report = await reproject(observations, constraints, StubJudge())

    assert report.changed == []
    assert len(report.skipped) == 1
    assert "no provenance" in report.skipped[0][1]


async def test_dangling_provenance_is_skipped_rather_than_partially_derived(
    tmp_path,
):
    """A link pointing at an observation the log does not have means the two
    tables came from different stores. Deriving from what remains would
    quietly produce a different rule."""
    observations, constraints = _stores(tmp_path)
    present = _observe(observations, "walk the dog at eight")
    stale = _stale_constraint(constraints, present)
    constraints.link_observation(stale.uid, "deadbeef" * 4)

    report = await reproject(observations, constraints, StubJudge())

    assert report.changed == []
    assert len(report.skipped) == 1
    assert "absent from the log" in report.skipped[0][1]


async def test_a_single_constraint_can_be_reprojected_alone(tmp_path):
    observations, constraints = _stores(tmp_path)
    one = _observe(observations, "swim on fridays")
    two = _observe(observations, "call mum on sundays")
    target = _stale_constraint(constraints, one)
    other = _stale_constraint(constraints, two)

    judge = StubJudge(
        tiers={"swim on fridays": Tier.DURABLE, "call mum on sundays": Tier.DURABLE},
        days_of_week={"swim on fridays": [4], "call mum on sundays": [6]},
    )
    report = await reproject(observations, constraints, judge, uid=target.uid)

    assert report.examined == 1
    assert constraints.get(target.uid).applicability.days_of_week == [4]
    assert constraints.get(other.uid).applicability.days_of_week == []


async def test_an_old_durable_statement_is_not_demoted_by_a_newer_aside(
    tmp_path,
):
    """Tier moves up only.

    Distinct from the whole-evidence test, where the durable statement happened
    to be the newest — so "any observation is durable" and "the newest is
    durable" gave the same answer and a demotion bug would have survived.
    """
    observations, constraints = _stores(tmp_path)
    declaration = _observe(observations, "I never work past six")
    aside = _observe(
        observations, "ran late today", at=WHEN + timedelta(days=2)
    )
    stale = _stale_constraint(constraints, declaration)
    constraints.link_observation(stale.uid, aside.uid)

    judge = StubJudge(
        tiers={"I never work past six": Tier.DURABLE},  # aside stays SESSION
    )
    await reproject(observations, constraints, judge)

    result = constraints.get(stale.uid)
    assert result.tier is Tier.DURABLE
    assert result.scope is Scope.PROFILE


async def test_created_at_moves_when_the_earliest_evidence_changes(tmp_path):
    """created_at is derived, not identity.

    It was absent from upsert's ON CONFLICT update clause, so re-projection
    could report the field as changed while the store discarded the write.
    """
    observations, constraints = _stores(tmp_path)
    early = _observe(observations, "sleep by eleven")
    stale = _stale_constraint(
        constraints, early, created_at=WHEN + timedelta(days=99)
    )
    judge = StubJudge(tiers={"sleep by eleven": Tier.DURABLE})

    await reproject(observations, constraints, judge)

    assert constraints.get(stale.uid).created_at == early.observed_at


async def test_a_folded_constraint_acquires_the_improvement_through_the_facade(
    tmp_path,
):
    """End-to-end through MemoryService, which is where #154 actually bit.

    The sequence that produced the ticket: a rule is stored by a build whose
    judge extracts no applicability, the judge improves, the user restates the
    rule — and the restatement folds, touching only last_observed_at, so the
    Tue/Thu rule keeps being served on a Monday. Re-projection is the only
    thing that closes it, and this asserts it does so through the same entry
    point a host calls.
    """
    from memory.service import MemoryService

    text = "physio on tuesdays and thursdays"
    db = str(tmp_path / "m.db")

    # Build one: durable, but no applicability extracted.
    old_judge = StubJudge(tiers={text: Tier.DURABLE})
    service = MemoryService(db, old_judge)
    first = await service.observe(
        text, channel=Channel.PLANNING, session_id="s1", observed_at=WHEN
    )
    assert first.stored
    monday = date(2026, 8, 3)
    assert len(service.get_active_constraints(monday)) == 1  # wrongly served

    # Build two: the judge now extracts applicability. The user restates the
    # rule, and it folds into the existing constraint.
    new_judge = StubJudge(
        tiers={text: Tier.DURABLE},
        days_of_week={text: [1, 3]},
        canonical={text: first.constraint_uid},
    )
    service._judge = new_judge
    await service.observe(
        text,
        channel=Channel.PLANNING,
        session_id="s2",
        observed_at=WHEN + timedelta(days=1),
    )
    # The fold alone does not close it — this is the defect, asserted.
    assert len(service.get_active_constraints(monday)) == 1

    report = await service.reproject()

    assert len(report.changed) == 1
    assert "applicability" in report.changed[0].fields
    assert service.get_active_constraints(monday) == []
    assert len(service.get_active_constraints(date(2026, 8, 4))) == 1  # Tuesday
