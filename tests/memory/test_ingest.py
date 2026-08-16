# tests/memory/test_ingest.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from memory.ingest import ingest
from memory.judge import StubJudge
from memory.models import Channel, Observation, Provenance, Tier
from memory.store import ObservationStore

T0 = datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


def _obs(text: str, session_id: str = "s1") -> Observation:
    return Observation(
        text=text,
        channel=Channel.PLANNING,
        provenance=Provenance.OBSERVED,
        session_id=session_id,
        observed_at=T0,
    )


async def test_ingest_stores_and_applies_the_anchor_judgement(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(anchors={"eat oats before gym": ["oats", "gym"]})
    result = await ingest(_obs("eat oats before gym"), judge, store)
    assert result.stored is True
    assert result.anchors == ["oats", "gym"]
    assert store.get(result.uid).anchors == ["oats", "gym"]


async def test_ingest_applies_the_tier_judgement(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(tiers={"never work after 21:00": Tier.DURABLE})
    result = await ingest(_obs("never work after 21:00"), judge, store)
    assert result.tier is Tier.DURABLE


async def test_meta_level_observations_are_not_stored(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge(metas={"begin the timeboxing session now": True})
    result = await ingest(_obs("begin the timeboxing session now"), judge, store)
    assert result.stored is False
    assert result.suppressed_as == "meta"
    assert store.all() == []


async def test_duplicates_are_not_stored(tmp_path):
    store = ObservationStore(str(tmp_path / "m.db"))
    first = _obs("wake at 07:00")
    store.append(first)
    judge = StubJudge(duplicates={"wake at 07:00": first.uid})
    result = await ingest(_obs("wake at 07:00"), judge, store)
    assert result.stored is False
    assert result.suppressed_as == "duplicate"
    assert len(store.all()) == 1


async def test_generated_provenance_is_never_judged_or_stored(tmp_path):
    """A rule's own output must not re-enter as evidence, and must not
    cost an LLM call to reject."""
    store = ObservationStore(str(tmp_path / "m.db"))
    judge = StubJudge()
    obs = Observation(
        text="pre-gym oats",
        channel=Channel.CALENDAR,
        provenance=Provenance.GENERATED,
        session_id="s1",
        observed_at=T0,
    )
    result = await ingest(obs, judge, store)
    assert result.stored is False
    assert result.suppressed_as == "generated"
    assert judge.calls == []


async def test_an_unknown_duplicate_id_raises_rather_than_discarding(tmp_path):
    """A hallucinated id must never silently destroy a real observation."""
    import pytest

    store = ObservationStore(str(tmp_path / "m.db"))
    store.append(_obs("wake at 07:00"))
    judge = StubJudge(duplicates={"eat oats before gym": "not-a-real-uid"})
    with pytest.raises(ValueError, match="unknown duplicate_of"):
        await ingest(_obs("eat oats before gym"), judge, store)
    assert len(store.all()) == 1


async def test_a_failing_judgement_propagates_and_stores_nothing(tmp_path):
    """Fail loudly is the contract; verify it holds at the ingest layer."""
    import pytest

    store = ObservationStore(str(tmp_path / "m.db"))

    class FailingJudge(StubJudge):
        async def tier(self, observation):
            raise ValueError("model returned nonsense")

    with pytest.raises(ValueError, match="model returned nonsense"):
        await ingest(_obs("eat oats before gym"), FailingJudge(), store)
    assert store.all() == []


async def test_the_four_judgements_are_issued_concurrently(tmp_path):
    """Four sequential round-trips is the failure this guards against."""
    store = ObservationStore(str(tmp_path / "m.db"))

    class SlowJudge(StubJudge):
        async def anchors(self, observation):
            await asyncio.sleep(0.05)
            return await super().anchors(observation)

        async def tier(self, observation):
            await asyncio.sleep(0.05)
            return await super().tier(observation)

        async def meta(self, observation):
            await asyncio.sleep(0.05)
            return await super().meta(observation)

        async def dedup(self, observation, recent):
            await asyncio.sleep(0.05)
            return await super().dedup(observation, recent)

    loop = asyncio.get_event_loop()
    start = loop.time()
    await ingest(_obs("eat oats before gym"), SlowJudge(), store)
    elapsed = loop.time() - start
    assert elapsed < 0.15, f"judgements appear sequential: {elapsed:.3f}s"
