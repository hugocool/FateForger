"""The card the user acted on becomes the receipt, and a failed edit never
costs the turn. Assertions are over ts values and stage indexes."""

from __future__ import annotations

import logging

import pytest

from fateforger.agents.timeboxing.session_contracts import Advance, GoBack
from fateforger.slack_bot.stage_card_registry import (
    StageCardRegistry,
    receipt_label,
)
from fateforger.slack_bot.stage_cards import (
    ApproveControl,
    Asking,
    BackControl,
    StageCard,
    stage,
)


class _Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.updates: list[dict] = []
        self._fail = fail

    async def chat_update(self, **payload):
        if self._fail:
            raise RuntimeError("slack is down")
        self.updates.append(dict(payload))
        return {"ok": True}


def _card(index: int, **update) -> StageCard:
    base = StageCard(
        stage=stage(index),
        session_key="C1:1.0",
        expected_revision=index,
        body=f"stage {index}",
        controls=[
            ApproveControl(artifact_id="a", artifact_revision=1, artifact_digest="a" * 64),
            BackControl(),
        ],
    )
    return base.model_copy(update=update)


@pytest.mark.asyncio
async def test_moving_on_turns_the_previous_card_into_a_receipt() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done="✅ confirmed",
        new_card=_card(4),
        channel="C1",
        ts="100.2",
        logger=logging.getLogger(__name__),
    )

    assert [u["ts"] for u in client.updates] == ["100.1"]
    receipt = client.updates[0]
    assert "✅ confirmed" in receipt["blocks"][0]["text"]["text"]
    assert not any(b.get("type") == "actions" for b in receipt["blocks"])
    shown = registry.shown("C1:1.0")
    assert shown is not None and shown.ts == "100.2" and shown.card.stage.index == 4


@pytest.mark.asyncio
async def test_a_failed_receipt_edit_is_swallowed_and_the_new_card_still_registers(caplog) -> None:
    registry = StageCardRegistry()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    with caplog.at_level(logging.WARNING):
        await registry.transition(
            _Client(fail=True),
            session_key="C1:1.0",
            done="✅ confirmed",
            new_card=_card(4),
            channel="C1",
            ts="100.2",
            logger=logging.getLogger("test"),
        )

    assert registry.shown("C1:1.0").ts == "100.2"
    assert any("receipt" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_no_done_label_leaves_the_previous_card_live() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done=None,
        new_card=None,
        channel="C1",
        ts="100.2",
        logger=logging.getLogger(__name__),
    )

    assert client.updates == []
    assert registry.shown("C1:1.0").ts == "100.1"


@pytest.mark.asyncio
async def test_a_card_at_the_same_message_is_not_receipted_over_itself() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(1))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done="✅ confirmed",
        new_card=_card(1, expected_revision=2),
        channel="C1",
        ts="100.1",
        logger=logging.getLogger(__name__),
    )

    assert client.updates == []
    assert registry.shown("C1:1.0").card.expected_revision == 2


@pytest.mark.asyncio
async def test_ending_the_session_forgets_the_card_after_receipting_it() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(3))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done="🚫 cancelled",
        new_card=None,
        channel="C1",
        ts="100.2",
        logger=logging.getLogger(__name__),
    )

    assert [u["ts"] for u in client.updates] == ["100.1"]
    assert registry.shown("C1:1.0") is None


def test_receipt_labels_come_from_the_intent_and_the_card() -> None:
    asked = _card(
        2,
        asking=Asking(requirement_id="r", question="?", why_needed="w"),
    )
    assert receipt_label(GoBack(), _card(3)) == "↩️ reopened"
    assert receipt_label(Advance(), asked) == "answered"
    assert receipt_label(Advance(), _card(3)) == "✅ confirmed"


def test_a_confirmed_day_receipt_names_the_day_that_was_accepted() -> None:
    """The date card's body is the day it *offered*; a typed change accepts a
    different one. The receipt is the only place the accepted day is written
    down on that card (#265)."""
    from datetime import date

    from fateforger.agents.timeboxing.session_contracts import (
        ConfirmPlanningDay,
        PlanningDay,
    )

    friday = PlanningDay.lock_default(
        value=date(2026, 9, 4), timezone="Europe/Amsterdam", lock_revision=2
    )
    label = receipt_label(ConfirmPlanningDay(planning_day=friday), _card(1))
    assert label.startswith("✅ ")
    assert "4 September" in label
    assert "working day" in label
