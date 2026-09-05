"""A required-block reminder reaches the user: the planning kind rides the
existing card, any other kind is one line in the DM."""
from __future__ import annotations

import pytest

from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.required_block_rule import REASON_MISSING, REQUIRED_BLOCK_KIND
from fateforger.slack_bot.planning import PlanningCoordinator

from .test_planning_reminder_suppression import DummyClient, _NoSlotRuntime


def _reminder(slug: str) -> PlanningReminder:
    return PlanningReminder(scope="U1", kind=REQUIRED_BLOCK_KIND, attempt=1,
                            message=f"Your `{slug}` block is not on today's calendar.",
                            user_id="U1", channel_id="D1", slug=slug, reason=REASON_MISSING)


@pytest.mark.asyncio
async def test_a_non_planning_kind_is_one_dm_line(monkeypatch):
    runtime = _NoSlotRuntime(ledger=None)
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    async def _dm(*, user_id): return "D1"
    monkeypatch.setattr(coordinator, "_resolve_dm_channel", _dm)
    async def _quiet(*, user_id, at): return False
    monkeypatch.setattr(coordinator, "_timeboxing_silences", _quiet)

    await coordinator.dispatch_planning_reminder(_reminder("sleep"))

    assert len(client.posted) == 1
    assert "`sleep`" in str(client.posted[0])
    assert not getattr(runtime, "event_draft_store", None) or not getattr(runtime.event_draft_store, "created", [])


@pytest.mark.asyncio
async def test_the_planning_kind_takes_the_existing_card_path(monkeypatch):
    runtime = _NoSlotRuntime(ledger=None)
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=DummyClient())  # type: ignore[arg-type]
    seen = []
    async def _card(reminder): seen.append(reminder)
    monkeypatch.setattr(coordinator, "_dispatch_planning_card", _card)
    async def _quiet(*, user_id, at): return False
    monkeypatch.setattr(coordinator, "_timeboxing_silences", _quiet)

    await coordinator.dispatch_planning_reminder(_reminder("planning"))

    assert seen and seen[0].slug == "planning"


@pytest.mark.asyncio
async def test_an_open_session_silences_a_required_block_reminder(monkeypatch):
    runtime = _NoSlotRuntime(ledger=None)
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    async def _busy(*, user_id, at): return True
    monkeypatch.setattr(coordinator, "_timeboxing_silences", _busy)

    await coordinator.dispatch_planning_reminder(_reminder("sleep"))

    assert client.posted == []
