# tests/memory/test_idempotent_write.py
"""Retrying a write must not manufacture evidence (#168).

`ingest` commits before `project` runs, so a projection failure leaves the
observation permanently stored. The duplication that follows is not caused by
that ordering, though — it is caused by the write having no identity until the
server invents one. `Observation.uid` is minted inside `observe`, so a retry
mints a fresh uuid and nothing in the call can say *this is the write I
already attempted*.

Give the write a caller-supplied identity and the ordering problem stops being
reachable, rather than being traded for a lost observation or a model
round-trip inside a write transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.identity import mint_uid
from memory.judge import StubJudge
from memory.models import Channel, Tier
from memory.service import MemoryService

WHEN = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
TEXT = "I collect my daughter from school at 15:00"


def _service(tmp_path, **kwargs):
    return MemoryService(
        str(tmp_path / "m.db"), StubJudge(tiers={TEXT: Tier.DURABLE}, **kwargs)
    )


async def test_a_retry_with_the_same_write_uid_is_a_no_op(tmp_path):
    service = _service(tmp_path)
    key = mint_uid()

    first = await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s",
        observed_at=WHEN, write_uid=key,
    )
    second = await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s",
        observed_at=WHEN, write_uid=key,
    )

    assert second.constraint_uid == first.constraint_uid
    assert len(service._observations.all()) == 1
    assert len(service._constraints.all()) == 1


async def test_without_a_write_uid_a_retry_still_duplicates(tmp_path):
    """The defect, asserted rather than described.

    This is what a harness retrying automatically does today, and every
    symptom is a plausible row: two observations that look exactly like the
    user having said the same thing twice.
    """
    service = _service(tmp_path)
    for _ in range(2):
        await service.observe(
            TEXT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
        )

    assert len(service._observations.all()) == 2


async def test_a_retry_adopts_the_orphan_a_failed_projection_left(tmp_path):
    """The case that motivated the ticket.

    ingest commits, projection then fails, and the observation is stored with
    no constraint linked. The retry must finish that projection rather than
    append a second observation beside the first.
    """
    service = _service(tmp_path)
    key = mint_uid()

    class ProjectionFails(StubJudge):
        async def canonicalise(self, observation, candidates):
            raise RuntimeError("host declined mid-projection")

    service._judge = ProjectionFails(tiers={TEXT: Tier.DURABLE})
    with pytest.raises(RuntimeError):
        await service.observe(
            TEXT, channel=Channel.PLANNING, session_id="s",
            observed_at=WHEN, write_uid=key,
        )

    # The orphan is real: stored, but nothing derived from it.
    assert len(service._observations.all()) == 1
    assert service._constraints.constraint_for_observation(key) is None
    assert len(service._constraints.all()) == 0

    service._judge = StubJudge(tiers={TEXT: Tier.DURABLE})
    outcome = await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s",
        observed_at=WHEN, write_uid=key,
    )

    assert outcome.stored
    assert len(service._observations.all()) == 1      # adopted, not duplicated
    assert len(service._constraints.all()) == 1
    assert service._constraints.constraint_for_observation(key) is not None


async def test_the_retry_does_not_re_ask_the_model_once_projected(tmp_path):
    """A completed write answers from the store.

    Otherwise the idempotent path costs the host a full set of judgements to
    return an answer it already has, and under a harness that retries on
    timeout the cost lands exactly where things are already slow.
    """
    service = _service(tmp_path)
    key = mint_uid()
    await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s",
        observed_at=WHEN, write_uid=key,
    )

    class Exploding(StubJudge):
        async def tier(self, observation):
            raise AssertionError("a settled write must not re-ask the model")

    service._judge = Exploding()
    outcome = await service.observe(
        TEXT, channel=Channel.PLANNING, session_id="s",
        observed_at=WHEN, write_uid=key,
    )
    assert outcome.stored


async def test_adopting_an_orphan_derives_from_what_was_stored_not_what_was_sent(
    tmp_path,
):
    """First payload wins, and L2 may never derive from what L1 does not hold.

    Reachable only where the two failure modes meet: a prior attempt appended
    and then failed during projection, and the retry arrives carrying different
    text — a mangled retry, or a caller reusing a key it should not. append is
    a no-op on a known uid, so the log keeps the original. If projection then
    ran on the incoming text, the constraint would be derived from a statement
    that exists nowhere in the log, with provenance pointing at a row that says
    something else. Re-projection would later "correct" it back, and the store
    would appear to change its mind unprompted.
    """
    original, mangled = TEXT, "something else entirely"
    service = MemoryService(
        str(tmp_path / "m.db"),
        StubJudge(tiers={original: Tier.DURABLE, mangled: Tier.DURABLE}),
    )
    key = mint_uid()

    class ProjectionFails(StubJudge):
        async def canonicalise(self, observation, candidates):
            raise RuntimeError("host declined mid-projection")

    service._judge = ProjectionFails(tiers={original: Tier.DURABLE})
    with pytest.raises(RuntimeError):
        await service.observe(
            original, channel=Channel.PLANNING, session_id="s",
            observed_at=WHEN, write_uid=key,
        )
    assert service._constraints.constraint_for_observation(key) is None

    service._judge = StubJudge(
        tiers={original: Tier.DURABLE, mangled: Tier.DURABLE}
    )
    outcome = await service.observe(
        mangled, channel=Channel.PLANNING, session_id="s",
        observed_at=WHEN, write_uid=key,
    )

    assert service._observations.get(key).text == original
    constraint = service._constraints.get(outcome.constraint_uid)
    assert constraint.description == original, (
        f"constraint derived from {constraint.description!r}, which is not in "
        f"the observation log"
    )
    assert len(service._observations.all()) == 1
