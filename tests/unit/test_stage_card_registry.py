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


def test_a_confirmed_day_receipt_body_is_the_day_that_was_accepted() -> None:
    """Live 2026-09-03: the receipt's label said "✅ Saturday 5 September —
    weekend day" and its body, minted when the card offered Thursday, still
    said "Planning 2026-09-03". The body is the accepted day or nothing."""
    from datetime import date

    from fateforger.agents.timeboxing.session_contracts import (
        ConfirmPlanningDay,
        PlanningDay,
    )
    from fateforger.slack_bot.stage_card_registry import receipt_body

    saturday = PlanningDay.lock_default(
        value=date(2026, 9, 5), timezone="Europe/Amsterdam", lock_revision=2
    )
    offered = _card(1, body="Planning 2026-09-03")
    assert receipt_body(ConfirmPlanningDay(planning_day=saturday), offered) == "Planning 2026-09-05"
    assert receipt_body(Advance(), offered) == "Planning 2026-09-03"


@pytest.mark.asyncio
async def test_a_receipt_can_carry_a_body_the_card_did_not_have() -> None:
    registry = StageCardRegistry()
    client = _Client()
    registry.remember("C1:1.0", channel="C1", ts="100.1", card=_card(1, body="Planning 2026-09-03"))

    await registry.transition(
        client,
        session_key="C1:1.0",
        done="✅ Saturday 5 September — weekend day",
        body="Planning 2026-09-05",
        new_card=_card(2),
        channel="C1",
        ts="100.2",
        logger=logging.getLogger(__name__),
    )

    sections = [
        b["text"]["text"] for b in client.updates[0]["blocks"] if b.get("type") == "section"
    ]
    assert any("Planning 2026-09-05" in t for t in sections)
    assert not any("2026-09-03" in t for t in sections)


from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    suspension_fact_id,
)


class _PostingClient(_Client):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(fail=fail)
        self.posts: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posts.append(dict(payload))
        return {"ok": True, "ts": f"200.{len(self.posts)}"}


def _snapshot_with(rows: list[str], *, day: date = date(2026, 9, 8), suspend: list[str] = ()):
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=day, timezone="Europe/Amsterdam", lock_revision=1),
        applicable_constraints=[
            {"uid": uid, "name": f"rule {uid}", "necessity": "must", "anchors": [], "fade": None} for uid in rows
        ],
        facts=[
            PlanningFact(fact_id=suspension_fact_id(u), kind=FactKind.SUSPENDED_CONSTRAINT,
                         value={"uid": u, "reason": "not today"}, source="user")
            for u in suspend
        ],
    )


async def _sync(registry, client, snapshot):
    await registry.sync_panel(
        client, session_key="C1:1.0", snapshot=snapshot, channel="C1", thread_ts="1.0",
        logger=logging.getLogger(__name__),
    )


@pytest.mark.asyncio
async def test_the_first_sync_posts_the_panel_and_remembers_its_ts() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    assert [p["thread_ts"] for p in client.posts] == ["1.0"]
    shown = registry.panel_shown("C1:1.0")
    assert shown is not None and (shown.ts, shown.thread_ts) == ("200.1", "1.0")
    assert shown.panel.first_shown_with == frozenset({"c1"})


@pytest.mark.asyncio
async def test_an_unchanged_row_set_does_nothing() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    await _sync(registry, client, _snapshot_with(["c1"]))
    assert len(client.posts) == 1 and client.updates == []


@pytest.mark.asyncio
async def test_a_suspension_edits_the_panel_in_place_and_keeps_first_shown_with() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1", "c2"]))
    await _sync(registry, client, _snapshot_with(["c1", "c2"], suspend=["c2"]))
    assert len(client.posts) == 1
    assert [u["ts"] for u in client.updates] == ["200.1"]
    assert registry.panel_shown("C1:1.0").panel.first_shown_with == frozenset({"c1", "c2"})


@pytest.mark.asyncio
async def test_a_day_change_receipts_the_old_panel_and_posts_a_new_one() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    await _sync(registry, client, _snapshot_with(["c1", "c9"], day=date(2026, 9, 9)))
    assert [u["ts"] for u in client.updates] == ["200.1"]
    assert "superseded" in client.updates[0]["blocks"][0]["text"]["text"]
    assert len(client.posts) == 2
    assert registry.panel_shown("C1:1.0").panel.first_shown_with == frozenset({"c1", "c9"})


@pytest.mark.asyncio
async def test_a_failed_edit_is_logged_and_the_record_stays() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    await _sync(registry, client, _snapshot_with(["c1"]))
    client._fail = True
    await _sync(registry, client, _snapshot_with(["c1"], suspend=["c1"]))  # no raise
    assert registry.panel_shown("C1:1.0").ts == "200.1"


@pytest.mark.asyncio
async def test_no_locked_day_means_no_panel() -> None:
    registry, client = StageCardRegistry(), _PostingClient()
    snapshot = _snapshot_with(["c1"]).model_copy(update={"planning_day": None})
    await _sync(registry, client, snapshot)
    assert client.posts == [] and registry.panel_shown("C1:1.0") is None
