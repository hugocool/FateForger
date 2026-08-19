# tests/memory/test_necessity.py
"""Necessity as its own judgement (#156).

The gap: necessity derived from `is_declaration`, which answers "did the
person state this outright" rather than "does breaking it ruin the day". Those
come apart exactly where it matters — people state preferences emphatically
and boundaries casually — and the result was MUST on 36 of 37 live
constraints, so a consumer filtering on it got everything and a planner went
rigid about things that should flex.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.constraint import Necessity
from memory.constraint_store import ConstraintStore
from memory.ingest import ingest
from memory.judge import StubJudge
from memory.models import Channel, Observation, Provenance, Tier
from memory.reprojection import reproject
from memory.service import MemoryService
from memory.store import ObservationStore

WHEN = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


def _obs(text, at=WHEN):
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        observed_at=at,
    )


async def test_necessity_is_asked_alongside_the_others_not_after(tmp_path):
    """It joins the concurrent gather.

    A sixth question that cost a sixth round-trip would be the usual reason
    someone folds it into an existing prompt to save a call — and the tier
    prompt already answers four things.
    """
    store = ObservationStore(str(tmp_path / "o.db"))
    judge = StubJudge(tiers={"school run at three": Tier.DURABLE})
    observation = _obs("school run at three")

    await ingest(observation, judge, store)

    asked = {question for question, _ in judge.calls}
    assert {"anchors", "tier", "meta", "dedup", "necessity"} <= asked


async def test_an_emphatic_preference_is_not_binding(tmp_path):
    """The case that made necessity a constant.

    Wired to is_declaration, "I ALWAYS..." was a declaration and therefore
    MUST. The question now asks what breaks instead of how it was said.
    """
    db = str(tmp_path / "m.db")
    text = "I ALWAYS start with deep work, never email first"
    judge = StubJudge(tiers={text: Tier.DURABLE}, declarations={text: True})
    service = MemoryService(db, judge)

    outcome = await service.observe(
        text, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )

    stored = service._constraints.get(outcome.constraint_uid)
    assert stored.necessity is Necessity.SHOULD


async def test_a_casually_stated_obligation_is_binding(tmp_path):
    db = str(tmp_path / "m.db")
    text = "oh and I've got the school run at 3"
    judge = StubJudge(
        tiers={text: Tier.DURABLE},
        declarations={text: False},   # not stated as a rule
        bindings={text: True},        # but breaking it is a failure
    )
    service = MemoryService(db, judge)

    outcome = await service.observe(
        text, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )

    stored = service._constraints.get(outcome.constraint_uid)
    assert stored.necessity is Necessity.MUST


async def test_a_binding_rule_is_not_softened_by_a_later_aside(tmp_path):
    """Binding once, binding after.

    Taking necessity from the newest observation would be last-write-wins,
    which this design rejects for tier and rejects here for the same reason.
    """
    db = str(tmp_path / "m.db")
    observations, constraints = ObservationStore(db), ConstraintStore(db)
    declaration = _obs("I collect my daughter at 15:00 every weekday")
    aside = _obs("ran a bit late for the school run", at=WHEN + timedelta(days=4))

    from memory.constraint import Applicability, Constraint, Scope, Source, Status
    from memory.models import DecayClass

    observations.append(declaration)
    observations.append(aside)
    constraint = Constraint(
        name="School run",
        description=declaration.text,
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[declaration.uid, aside.uid],
        created_at=WHEN,
        decay_class=DecayClass.PERMANENT,
        last_observed_at=WHEN,
    )
    constraints.upsert(constraint)

    judge = StubJudge(
        tiers={declaration.text: Tier.DURABLE, aside.text: Tier.DURABLE},
        bindings={declaration.text: True},   # the aside says nothing binding
    )
    await reproject(observations, constraints, judge)

    assert constraints.get(constraint.uid).necessity is Necessity.MUST


async def test_the_improvement_reaches_constraints_that_already_exist(tmp_path):
    """Why #155 and #154 came first.

    Two rules stored by a build that made everything MUST. The judgement
    improves. Without re-projection both stay MUST forever and a consumer
    still cannot tell them apart; with it, they separate — which is the whole
    return on the two tickets before this one.
    """
    db = str(tmp_path / "m.db")
    boundary = "I collect my daughter at 15:00"
    preference = "I like starting with deep work"

    old_judge = StubJudge(
        tiers={boundary: Tier.DURABLE, preference: Tier.DURABLE},
        bindings={boundary: True, preference: True},   # the old, wrong wiring
    )
    service = MemoryService(db, old_judge)
    for text in (boundary, preference):
        await service.observe(
            text, channel=Channel.PLANNING, session_id=text, observed_at=WHEN
        )
    assert all(
        c.necessity is Necessity.MUST for c in service._constraints.all()
    )

    service._judge = StubJudge(
        tiers={boundary: Tier.DURABLE, preference: Tier.DURABLE},
        bindings={boundary: True},   # preference is no longer binding
    )
    report = await service.reproject()

    assert len(report.changed) == 1
    by_description = {c.description: c for c in service._constraints.all()}
    assert by_description[boundary].necessity is Necessity.MUST
    assert by_description[preference].necessity is Necessity.SHOULD


async def test_a_session_constraint_also_derives_necessity_from_the_judgement(
    tmp_path,
):
    """The non-durable create branch.

    Mutation testing found it: reverting only this branch to is_declaration
    changed no test result, because every necessity test happened to go
    through the durable path.
    """
    from memory.ingest import IngestResult
    from memory.projection import project

    store = ConstraintStore(str(tmp_path / "c.db"))
    observation = _obs("I ALWAYS block the afternoon on demo days")
    result = IngestResult(
        stored=True,
        uid=observation.uid,
        tier=Tier.SESSION,
        label="Demo day afternoon",
        is_declaration=True,
        is_binding=False,
    )

    constraint = await project(observation, result, StubJudge(), store)

    assert constraint.necessity is Necessity.SHOULD
