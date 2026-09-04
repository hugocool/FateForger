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


def _judge() -> StubJudge:
    return StubJudge(
        tiers={RULE: Tier.DURABLE},
        labels={RULE: "Planning session"},
        day_types={RULE: ["working"]},
        requires_blocks={RULE: "planning"},
        anchors={RULE: ["planning session"]},
    )


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
    with pytest.raises(DuplicateKind):
        await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert len(service.get_active_constraints(date(2026, 9, 7), day_type="working")) == before


async def test_a_malformed_slug_is_refused_before_anything_is_written(tmp_path):
    service = MemoryService(str(tmp_path / "m.db"), _judge())
    with pytest.raises(ValueError):
        await service.promote_kind("Planning Session", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert service.kinds() == []


async def test_a_failed_observe_unregisters_the_kind(tmp_path):
    class _Fails(StubJudge):
        async def tier(self, observation):
            raise RuntimeError("model down")

    service = MemoryService(str(tmp_path / "m.db"), _Fails(anchors={RULE: ["planning session"]}))
    with pytest.raises(RuntimeError):
        await service.promote_kind("planning", anchor_name="planning session", rule_text=RULE, observed_at=T0)
    assert service.kinds() == []
