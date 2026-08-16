# tests/memory/test_projection.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.constraint import Applicability, Constraint
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
        necessity="must",
        scope="profile",
        status="proposed",
        source="user",
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=["obs-0"],
        created_at=T0,
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
