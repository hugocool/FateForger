# tests/memory/test_decay_projection.py
from __future__ import annotations

from datetime import datetime, timezone

from memory.constraint_store import ConstraintStore
from memory.ingest import IngestResult
from memory.judge import StubJudge, TierJudgement
from memory.models import Channel, DecayClass, Observation, Provenance, Tier
from memory.projection import project

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, at: datetime = T0) -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id="s1",
        observed_at=at,
    )


def _result(obs, decay=DecayClass.PERMANENT, label="a rule") -> IngestResult:
    return IngestResult(
        stored=True, uid=obs.uid, tier=Tier.DURABLE,
        label=label, is_declaration=True, decay_class=decay,
    )


def test_the_default_is_permanent():
    """The safe direction: an unjudged rule must never fade."""
    assert TierJudgement(label="x").decay_class is DecayClass.PERMANENT
    assert IngestResult(stored=True).decay_class is DecayClass.PERMANENT


def test_stub_returns_its_canned_class():
    import asyncio

    judge = StubJudge(decay_classes={"cap framing at 15m": DecayClass.PROJECT})
    r = asyncio.get_event_loop().run_until_complete(
        judge.tier(_obs("cap framing at 15m"))
    )
    assert r.decay_class is DecayClass.PROJECT


async def test_the_class_reaches_the_constraint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("cap framing at 15 minutes")
    c = await project(obs, _result(obs, DecayClass.PROJECT), StubJudge(), store)
    assert c.decay_class is DecayClass.PROJECT
    assert store.get(c.uid).decay_class is DecayClass.PROJECT


async def test_last_observed_at_starts_at_the_observation(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    obs = _obs("sleep at 23:00")
    c = await project(obs, _result(obs), StubJudge(), store)
    assert c.last_observed_at == T0


async def test_folding_a_later_observation_advances_last_observed_at(tmp_path):
    """Re-observation is what revives a fading rule."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    first = _obs("cap framing at 15 minutes", T0)
    c = await project(first, _result(first, DecayClass.PROJECT), StubJudge(), store)

    later = _obs("still capping framing at 15 minutes", T1)
    judge = StubJudge(canonical={"still capping framing at 15 minutes": c.uid})
    folded = await project(later, _result(later, DecayClass.PROJECT), judge, store)

    assert folded.uid == c.uid
    assert store.get(c.uid).last_observed_at == T1


async def test_folding_an_earlier_observation_does_not_rewind(tmp_path):
    """Backfill replays out of order; the newest evidence must win."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    recent = _obs("cap framing", T1)
    c = await project(recent, _result(recent, DecayClass.PROJECT), StubJudge(), store)

    old = _obs("capping framing", T0)
    judge = StubJudge(canonical={"capping framing": c.uid})
    await project(old, _result(old, DecayClass.PROJECT), judge, store)

    assert store.get(c.uid).last_observed_at == T1


async def test_a_live_aware_observation_folds_onto_a_naive_seeded_constraint(tmp_path):
    """The seeded store holds naive timestamps; the live path stamps aware UTC."""
    from datetime import timezone as _tz

    store = ConstraintStore(str(tmp_path / "c.db"))
    naive = datetime(2026, 3, 1, 9, 0)  # no tzinfo, as the backfill produces
    first = _obs("cap framing at 15 minutes", naive)
    c = await project(first, _result(first, DecayClass.PROJECT), StubJudge(), store)

    aware = datetime(2026, 8, 17, 9, 0, tzinfo=_tz.utc)
    later = _obs("still capping framing", aware)
    judge = StubJudge(canonical={"still capping framing": c.uid})
    folded = await project(later, _result(later, DecayClass.PROJECT), judge, store)

    assert folded.uid == c.uid
    assert store.get(c.uid).last_observed_at == aware


def test_naive_timestamps_are_coerced_to_utc():
    from datetime import timezone as _tz

    from memory.models import as_aware_utc

    naive = datetime(2026, 3, 1, 9, 0)
    assert as_aware_utc(naive) == datetime(2026, 3, 1, 9, 0, tzinfo=_tz.utc)
    already = datetime(2026, 3, 1, 9, 0, tzinfo=_tz.utc)
    assert as_aware_utc(already) is already or as_aware_utc(already) == already
