# tests/memory/test_concurrent_ingest.py
"""Two statements arriving at once on one session (#168's cause, not its shape).

The Slack host is to prefetch the way the legacy agent did — constraint
prefetch, durable prefetch and calendar immovables issued concurrently and
gated by stage — so `observe` will be called alongside reads, and alongside
other observes, on the same session. The spec's known-gaps section had this
as assumed rather than tested.

Measured before the fix: two concurrent identical observes produced two
observations where two sequential ones produced one. Both read the session's
history before either appended, so neither saw the other. The dedup judge was
correct; the candidate list was stale.

`write_uid` cannot help here. It makes a *retry* idempotent, and two genuinely
concurrent statements carry different write ids.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from memory.judge import DedupJudgement, StubJudge
from memory.models import Channel, Tier
from memory.service import MemoryService

WHEN = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
TEXT = "I collect my daughter at 15:00"
OTHER = "no meetings before ten"


class _RealisticDedup(StubJudge):
    """Answers correctly given a correct candidate list, and takes time doing
    it — which is what opens the window."""

    async def dedup(self, observation, recent):
        await asyncio.sleep(0.01)
        match = next(
            (o.uid for o in recent if o.text == observation.text), None
        )
        return DedupJudgement(duplicate_of=match)


def _service(tmp_path, name="m.db"):
    return MemoryService(
        str(tmp_path / name),
        _RealisticDedup(tiers={TEXT: Tier.DURABLE, OTHER: Tier.DURABLE}),
    )


async def test_concurrent_restatements_on_one_session_dedup_once(tmp_path):
    service = _service(tmp_path)

    await asyncio.gather(
        *(
            service.observe(
                TEXT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
            )
            for _ in range(4)
        )
    )

    assert len(service._observations.all()) == 1, (
        "a restatement was appended twice; L1 is append-only so the duplicate "
        "is permanent, and evidence is what promotion and decay count"
    )


async def test_it_matches_what_sequential_calls_produce(tmp_path):
    """The property that makes concurrency invisible when it is correct."""
    concurrent = _service(tmp_path, "a.db")
    await asyncio.gather(
        *(
            concurrent.observe(
                TEXT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
            )
            for _ in range(3)
        )
    )

    sequential = _service(tmp_path, "b.db")
    for _ in range(3):
        await sequential.observe(
            TEXT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
        )

    assert len(concurrent._observations.all()) == len(
        sequential._observations.all()
    )
    assert len(concurrent._constraints.all()) == len(
        sequential._constraints.all()
    )


async def test_different_sessions_are_not_serialised_against_each_other(
    tmp_path,
):
    """A host running several conversations must not have one wait on another.

    The lock is per session for this reason; a per-store lock would have been
    simpler and would have made every concurrent conversation queue behind
    whichever one was mid-judgement.
    """
    service = _service(tmp_path)

    started = asyncio.Event()
    release = asyncio.Event()

    class Blocking(_RealisticDedup):
        async def dedup(self, observation, recent):
            if observation.session_id == "slow":
                started.set()
                await release.wait()
            return await super().dedup(observation, recent)

    service._judge = Blocking(tiers={TEXT: Tier.DURABLE, OTHER: Tier.DURABLE})

    slow = asyncio.create_task(
        service.observe(
            TEXT, channel=Channel.PLANNING, session_id="slow", observed_at=WHEN
        )
    )
    await started.wait()

    # A different session must complete while `slow` is still blocked.
    await asyncio.wait_for(
        service.observe(
            OTHER, channel=Channel.PLANNING, session_id="other", observed_at=WHEN
        ),
        timeout=2,
    )

    release.set()
    await slow


async def test_an_unscoped_observation_takes_no_lock(tmp_path):
    """Dedup only ever compares within a session, so there is nothing for an
    unscoped observation to be stale against."""
    service = _service(tmp_path)

    await asyncio.gather(
        *(
            service.observe(
                TEXT, channel=Channel.PLANNING, session_id=None, observed_at=WHEN
            )
            for _ in range(3)
        )
    )

    # No dedup is possible without a session, so all three are stored — the
    # point is that they are stored concurrently without serialising.
    assert len(service._observations.all()) == 3
