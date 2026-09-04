# tests/memory/test_projection.py
from __future__ import annotations

from datetime import datetime, timezone

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
from memory.ingest import IngestResult
from memory.judge import StubJudge
from memory.models import Channel, Observation, Provenance, Tier
from memory.projection import project

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=T0,
    )


def _result(tier: Tier = Tier.DURABLE, uid: str = "obs-1") -> IngestResult:
    return IngestResult(stored=True, uid=uid, tier=tier, anchors=["gym"])


def _existing(store: ConstraintStore, name: str) -> Constraint:
    c = Constraint(
        name=name,
        description=name,
        necessity=Necessity.MUST,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=["obs-0"],
        created_at=T0,
        last_observed_at=T0,
    )
    store.upsert(c)
    return c


async def test_a_new_observation_creates_a_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("eat oats two hours before gym")
    c = await project(obs, _result(), StubJudge(), store)
    assert c.uid
    assert c.tier is Tier.DURABLE
    assert c.source_observation_uids == [obs.uid]
    assert len(store.all()) == 1


async def test_a_restatement_joins_the_existing_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    existing = _existing(store, "Oats before gym")
    obs = _obs("oats 2h before the gym")
    judge = StubJudge(canonical={"oats 2h before the gym": existing.uid})
    c = await project(obs, _result(), judge, store)
    assert c.uid == existing.uid
    assert obs.uid in c.source_observation_uids
    assert "obs-0" in c.source_observation_uids
    assert len(store.all()) == 1, "must not create a second constraint"


async def test_an_unknown_constraint_uid_raises(tmp_path):
    """Never act on a model-supplied id that was never minted."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    _existing(store, "Oats before gym")
    judge = StubJudge(canonical={"anything": "not-a-real-uid"})
    with pytest.raises(ValueError, match="unknown constraint_uid"):
        await project(_obs("anything"), _result(), judge, store)


async def test_a_session_tier_observation_still_projects(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = await project(_obs("hockey at 11:45"), _result(Tier.SESSION), StubJudge(), store)
    assert c.tier is Tier.SESSION


async def test_a_session_restatement_never_demotes_a_durable_constraint(tmp_path):
    """The cross-session recall failure this project exists to fix."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    existing = _existing(store, "Oats before gym")
    judge = StubJudge(canonical={"oats at 9 today": existing.uid})
    await project(_obs("oats at 9 today"), _result(Tier.SESSION), judge, store)
    assert store.get(existing.uid).tier is Tier.DURABLE


async def test_a_session_observation_is_not_canonicalised(tmp_path):
    """Session tier is mortal and uncanonicalised; asking would waste a call."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    _existing(store, "Oats before gym")
    judge = StubJudge()
    await project(_obs("hockey at 11:45"), _result(Tier.SESSION), judge, store)
    assert judge.calls == [], "no judgement should have been requested"


async def test_refuses_to_project_a_suppressed_observation(tmp_path):
    """Provenance must never point at something absent from the log."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    suppressed = IngestResult(stored=False, suppressed_as="meta")
    with pytest.raises(ValueError, match="not stored"):
        await project(_obs("begin the session"), suppressed, StubJudge(), store)
    assert store.all() == []


async def test_concurrent_projections_of_the_same_rule_create_one_constraint(tmp_path):
    """The canonicalisation layer must not produce duplicates under concurrency."""
    import asyncio

    store = ConstraintStore(str(tmp_path / "c.db"))

    class SlowJudge(StubJudge):
        """Answers 'new' the first time, then folds into whatever exists."""

        async def canonicalise(self, observation, candidates):
            await asyncio.sleep(0.02)
            self.calls.append(("canonicalise", observation.uid))
            from memory.judge import CanonicaliseJudgement

            if candidates:
                return CanonicaliseJudgement(constraint_uid=candidates[0].uid)
            return CanonicaliseJudgement()

    judge = SlowJudge()
    await asyncio.gather(
        project(_obs("oats before gym"), _result(), judge, store),
        project(_obs("oats 2h before the gym"), _result(), judge, store),
    )
    assert len(store.all()) == 1, "concurrent projection created a duplicate"


async def test_the_models_label_becomes_the_constraint_name(tmp_path):
    """I1: the LLM proposes and names. Name and description must differ."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    judge = StubJudge(
        tiers={"eat oats two hours before gym": Tier.DURABLE},
        labels={"eat oats two hours before gym": "Oats before gym"},
    )
    obs = _obs("eat oats two hours before gym")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Oats before gym",
    )
    c = await project(obs, result, judge, store)
    assert c.name == "Oats before gym"
    assert c.description == "eat oats two hours before gym"


async def test_a_binding_rule_becomes_a_must(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("I collect my daughter from school at 15:00")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="School run", is_binding=True,
    )
    c = await project(obs, result, StubJudge(), store)
    assert c.necessity is Necessity.MUST


async def test_a_preference_becomes_a_should_however_firmly_stated(tmp_path):
    """Necessity follows what breaks, not how emphatically it was said.

    Wiring it to is_declaration made this MUST, which is why 36 of 37 live
    constraints were MUST and no consumer could tell a boundary from a
    preference (#156).
    """
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("I ALWAYS start the day with deep work, never email")
    result = IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label="Deep work first", is_binding=False,
    )
    c = await project(obs, result, StubJudge(), store)
    assert c.necessity is Necessity.SHOULD


async def test_channel_maps_to_the_consumers_source_vocabulary(tmp_path):
    """The consuming server's enum is user|calendar|system|feedback."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    for channel, expected in (
        (Channel.PLANNING, Source.USER),
        (Channel.REVIEW, Source.USER),
        (Channel.CALENDAR, Source.CALENDAR),
    ):
        obs = Observation(
            text=f"rule from {channel.value}",
            channel=channel,
            provenance=Provenance.OBSERVED,
            session_id="s1",
            observed_at=T0,
        )
        result = IngestResult(
            stored=True, uid=obs.uid, tier=Tier.DURABLE,
            label="a rule",
        )
        c = await project(obs, result, StubJudge(), store)
        assert c.source is expected


async def test_projection_writes_day_types_into_applicability(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    result = IngestResult(
        stored=True, uid="obs-1", tier=Tier.DURABLE, day_types=["working"]
    )
    c = await project(_obs("planning session on working days"), result, StubJudge(), store)
    assert c.applicability.day_types == ["working"]
    assert store.get(c.uid).applicability.day_types == ["working"]


async def test_a_durable_rule_carries_its_required_kind(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    result = IngestResult(stored=True, uid="obs-1", tier=Tier.DURABLE, requires_block="planning")
    c = await project(_obs("every working day has a planning session"), result, StubJudge(), store)
    assert c.requires_block == "planning"
    assert store.get(c.uid).to_view().requires_block == "planning"


async def test_a_session_fact_never_carries_a_required_kind(tmp_path):
    """Durable-only (spec decision 10): 'plan tomorrow's session at 17:00' is a
    fact for the planner, not a standing requirement."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    result = IngestResult(stored=True, uid="obs-1", tier=Tier.SESSION, requires_block="planning")
    c = await project(_obs("plan tomorrow's session at 17:00"), result, StubJudge(), store)
    assert c.requires_block is None


async def test_a_fold_sets_the_required_kind_once_and_never_unsets_it(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    existing = _existing(store, "planning session")
    judge = StubJudge(canonical={"we plan every working day": existing.uid,
                                 "planning again": existing.uid})
    folded = await project(
        _obs("we plan every working day"),
        IngestResult(stored=True, uid="obs-1", tier=Tier.DURABLE, requires_block="planning"),
        judge, store,
    )
    assert folded.requires_block == "planning"
    again = await project(
        _obs("planning again"),
        IngestResult(stored=True, uid="obs-2", tier=Tier.DURABLE, requires_block=None),
        judge, store,
    )
    assert again.requires_block == "planning"
