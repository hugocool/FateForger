"""An instruction to change the plan on the table is not a rule about life (#287).

On 2026-09-03 seven planning-thread messages were observed. Five were filed
correctly. Two were instructions addressed to the planner about the artifact
it had just shown, and both became standing profile rules:

    "Make the finances block 30 minutes instead of 45, everything else stays"
        -> durable, necessity MUST
    "Move deep work block 30 minutes earlier and add a 15-minute buffer after it"
        -> durable

Both then appeared in `memory_get_active_constraints(2026-09-04, working)` as
standing rules. The first would have capped every future finances block at 30
minutes because of one afternoon's edit.

Nothing in the six judgements could catch this. `META_PROMPT` explicitly routes
it away -- it says rules about "how long blocks should be" are NOT meta -- and
the tier judge then reads an unscoped imperative as a standing preference. The
discriminator that was missing: a rule still means something with no plan on
the screen, and an edit does not.

The kernel already carries these as `FactKind.REVISION_INSTRUCTION` inside the
planning session, where the planner reads them. Memory storing them too, at any
tier, is what makes one day's edit look like a rule.
"""
from __future__ import annotations

from datetime import datetime, timezone

from memory.judge import StubJudge
from memory.models import Channel, Tier
from memory.service import MemoryService

WHEN = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc)

EDIT = "Make the finances block 30 minutes instead of 45, everything else stays"
RULE = "Deep Work blocks are usually 2 hours long"


def _service(tmp_path, **kwargs) -> MemoryService:
    return MemoryService(str(tmp_path / "m.db"), StubJudge(**kwargs))


async def test_an_edit_to_the_current_plan_is_not_stored(tmp_path) -> None:
    service = _service(tmp_path, edits={EDIT: True}, tiers={EDIT: Tier.DURABLE})

    outcome = await service.observe(
        EDIT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )

    assert outcome.stored is False
    assert outcome.suppressed_as == "edit"
    assert service._observations.all() == []
    assert service._constraints.all() == []


async def test_a_rule_about_the_schedule_is_still_stored(tmp_path) -> None:
    """The line the meta prompt draws stays where it was.

    "How long blocks should be" is a rule about the person's days. Only an
    instruction to change a plan that already exists is suppressed.
    """
    service = _service(tmp_path, edits={RULE: False}, tiers={RULE: Tier.DURABLE})

    outcome = await service.observe(
        RULE, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )

    assert outcome.stored is True
    assert outcome.constraint_uid is not None


async def test_the_edit_judgement_runs_for_every_observation(tmp_path) -> None:
    """Concurrently with the other six, so it costs no latency (CLAUDE.md)."""
    judge = StubJudge(edits={RULE: False}, tiers={RULE: Tier.DURABLE})
    service = MemoryService(str(tmp_path / "m.db"), judge)

    await service.observe(
        RULE, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )

    assert any(name == "edit" for name, _ in judge.calls)


async def test_an_edit_is_refused_before_it_can_be_deduped_against(tmp_path) -> None:
    """Suppression must leave nothing behind for a later statement to fold into.

    A stored edit would become a dedup candidate, so the next similar edit
    would restate it and refresh its timestamp -- which is how a one-off
    acquires the appearance of a recurring rule.
    """
    service = _service(tmp_path, edits={EDIT: True}, tiers={EDIT: Tier.DURABLE})

    await service.observe(
        EDIT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )
    await service.observe(
        EDIT, channel=Channel.PLANNING, session_id="s", observed_at=WHEN
    )

    assert service._observations.all() == []
