# tests/memory/test_requires_block.py
"""Promotion: the one write that mints an enforceable kind (spec §1).

Asks-first lives in the host; here the server's half is asserted: a kind is
registered, its anchor resolved through the judge, the rule stated as a
durable observation, and the projected rule carries the kind. Never minted by
observe: the kind row exists only because promote_kind wrote it.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from memory.judge import StubJudge
from memory.kind_store import DuplicateKind
from memory.models import Tier
from memory.service import MemoryService

T0 = datetime(2026, 9, 4, 9, 0, tzinfo=timezone.utc)
RULE = "Every working day has a planning session in which the next day is timeboxed."


# One set of canned answers, shared so the failure-mode judges below differ from
# the working one only in the way each is meant to fail.
ANSWERS = dict(
    tiers={RULE: Tier.DURABLE},
    labels={RULE: "Planning session"},
    day_types={RULE: ["working"]},
    requires_blocks={RULE: "planning"},
    anchors={RULE: ["planning session"]},
)


def _judge() -> StubJudge:
    return StubJudge(**ANSWERS)


async def test_promotion_registers_the_kind_and_the_rule_carries_it(tmp_path):
    service = MemoryService(str(tmp_path / "m.db"), _judge())
    outcome = await service.promote_kind(
        "planning", anchor_name="planning session", rule_text=RULE, observed_at=T0
    )
    assert outcome.slug == "planning"
    assert outcome.anchor_created is True
    assert outcome.requires_block_recorded is True
    views = service.get_active_constraints(date(2026, 9, 7), day_type="working")
    assert [v.requires_block for v in views if v.uid == outcome.constraint_uid] == ["planning"]
    assert service.kinds() == ["planning"]


async def test_a_rule_observed_before_any_kind_exists_records_no_requirement(tmp_path):
    """observe never mints a kind: with an empty registry the sixth judgement is
    not even asked, and the rule is stored without a required kind."""
    from memory.models import Channel

    service = MemoryService(str(tmp_path / "m.db"), _judge())
    outcome = await service.observe(
        RULE, channel=Channel.PLANNING, session_id="s1", observed_at=T0
    )
    assert outcome.stored is True
    assert service.kinds() == []
    views = service.get_active_constraints(date(2026, 9, 7), day_type="working")
    assert all(v.requires_block is None for v in views)


async def test_a_duplicate_promotion_is_refused_and_writes_nothing(tmp_path):
    service = MemoryService(str(tmp_path / "m.db"), _judge())
    await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    before = len(service.get_active_constraints(date(2026, 9, 7), day_type="working"))
    with pytest.raises(DuplicateKind) as excinfo:
        await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    # Spec §5 names the code; the MCP tool surfaces the message as-is, so it
    # travels with it rather than only in the class name.
    assert DuplicateKind.code == "duplicate_kind"
    assert f"[{DuplicateKind.code}]" in str(excinfo.value)
    assert len(service.get_active_constraints(date(2026, 9, 7), day_type="working")) == before


async def test_a_malformed_slug_is_refused_before_anything_is_written(tmp_path):
    service = MemoryService(str(tmp_path / "m.db"), _judge())
    with pytest.raises(ValueError):
        await service.promote_kind("Planning Session", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert service.kinds() == []


async def test_a_failed_observe_registers_no_kind_at_all(tmp_path):
    """Nothing to unregister: the rule is stored first, so a promotion that
    cannot even state its rule never reaches the registry."""
    class _Fails(StubJudge):
        async def tier(self, observation):
            raise RuntimeError("model down")

    service = MemoryService(str(tmp_path / "m.db"), _Fails(anchors={RULE: ["planning session"]}))
    with pytest.raises(RuntimeError):
        await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert service.kinds() == []


async def test_a_retry_finishes_the_promotion_rather_than_restating_the_rule(tmp_path):
    """A failure between storing the rule and registering the kind is retryable.

    The promotion's write_uid is minted from the slug, so the second attempt
    adopts the observation the first one left behind instead of appending a
    second copy. L1 is append-only, so a duplicate would be permanent, and
    evidence is what promotion and decay count.
    """
    class _FailsOnce(StubJudge):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.projections = 0

        async def canonicalise(self, observation, candidates):
            self.projections += 1
            if self.projections == 1:
                raise RuntimeError("model down after the rule was stored")
            return await StubJudge.canonicalise(self, observation, candidates)

    service = MemoryService(str(tmp_path / "m.db"), _FailsOnce(**ANSWERS))
    with pytest.raises(RuntimeError):
        await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert service.kinds() == []
    assert len(service._observations.all()) == 1, "the rule was stored before it failed"

    outcome = await service.promote_kind(
        "planning", anchor_name="planning session", rule_text=RULE, observed_at=T0
    )
    assert outcome.requires_block_recorded is True
    assert service.kinds() == ["planning"]
    assert len(service._observations.all()) == 1, "the retry adopted the orphan"


async def test_a_rule_the_judge_does_not_map_to_the_kind_fails_the_promotion(tmp_path):
    """The registry row and the rule that justifies it are promoted together.

    The judge read the promotion's own rule with the kind on offer and did not
    say it requires one, so nothing in the store would ever ask for a block of
    this kind: the kind is removed and the caller is told.

    The rule itself stays. It is a legitimate durable statement the user made,
    the log is append-only (I2), and quietly un-storing it would be a silent
    half of a failure the caller is already being told about loudly.
    """
    answers = dict(ANSWERS)
    answers.pop("requires_blocks")
    service = MemoryService(str(tmp_path / "m.db"), StubJudge(**answers))
    with pytest.raises(ValueError) as excinfo:
        await service.promote_kind(
            "planning", anchor_name="planning session", rule_text=RULE, observed_at=T0
        )
    assert "planning" in str(excinfo.value)
    assert service.kinds() == []
    assert [o.text for o in service._observations.all()] == [RULE]
    stored = service._constraints.all()
    assert [c.requires_block for c in stored] == [None]


async def test_a_concurrent_observe_is_never_offered_a_kind_that_is_rolled_back(tmp_path):
    """No window exists in which a kind row outlives the rule behind it.

    The registry used to be written before the rule was observed, so a
    promotion that failed afterwards left a kind visible to anything observing
    concurrently -- and the constraint that observe stored named a slug the
    registry no longer held. Nothing downstream could tell that apart from a
    kind someone had removed on purpose.

    The gate is what makes this a test rather than two writes in a row: without
    it the stub judge never suspends and the second write starts after the
    first has finished.
    """
    import asyncio

    from memory.models import Channel

    OTHER = "the end-of-day closure block updates the board"
    gate = asyncio.Event()

    class _FailsAfterTheRuleIsStored(StubJudge):
        async def tier(self, observation):
            if observation.session_id == "promotion:planning":
                # Past the point the old order registered the kind at.
                gate.set()
                await asyncio.sleep(0)
            return await StubJudge.tier(self, observation)

        async def canonicalise(self, observation, candidates):
            if observation.session_id == "promotion:planning":
                raise RuntimeError("model down after the rule was stored")
            return await StubJudge.canonicalise(self, observation, candidates)

    judge = _FailsAfterTheRuleIsStored(
        tiers={RULE: Tier.DURABLE, OTHER: Tier.DURABLE},
        labels={RULE: "Planning session", OTHER: "Closure block"},
        requires_blocks={RULE: "planning", OTHER: "planning"},
        anchors={RULE: ["planning session"], OTHER: ["planning session"]},
    )
    service = MemoryService(str(tmp_path / "m.db"), judge)

    async def observer():
        await gate.wait()
        return await service.observe(
            OTHER, channel=Channel.PLANNING, session_id="s1", observed_at=T0
        )

    promotion, _ = await asyncio.gather(
        service.promote_kind(
            "planning", anchor_name="planning session", rule_text=RULE, observed_at=T0
        ),
        observer(),
        return_exceptions=True,
    )
    assert isinstance(promotion, RuntimeError)
    registered = service.kinds()
    for constraint in service._constraints.all():
        assert constraint.requires_block is None or constraint.requires_block in registered, (
            f"constraint {constraint.uid} requires {constraint.requires_block!r}, "
            f"which the registry does not hold: {registered}"
        )
