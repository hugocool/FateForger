"""A required-block reminder reaches the user.

Every rung is revalidated against the watcher's own check before it posts (R3):
a scheduled nudge is a claim about the calendar minutes or hours ago, and the
block may be back. One DM line for every kind, `planning` included -- the
planning card books a block, and a `moved_out` planning block already exists.
"""
from __future__ import annotations

import pytest

from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.required_block_rule import (
    REASON_MISSING,
    REASON_MOVED_OUT,
    REQUIRED_BLOCK_KIND,
)
from fateforger.slack_bot.planning import PlanningCoordinator

from .test_planning_reminder_suppression import DummyClient, _NoSlotRuntime


def _reminder(slug: str, reason: str = REASON_MISSING) -> PlanningReminder:
    return PlanningReminder(scope="U1", kind=REQUIRED_BLOCK_KIND, attempt=1,
                            message=f"Your `{slug}` block is not on today's calendar.",
                            user_id="U1", channel_id="D1", slug=slug, reason=reason)


class _Watcher:
    """The rule the runtime hands the dispatcher, recording what it was asked."""

    def __init__(self, verdict: str | None):
        self._verdict, self.asked = verdict, []

    async def recheck(self, *, user_id, slug, now):
        self.asked.append((user_id, slug, now))
        return self._verdict


def _coordinator(monkeypatch, *, watcher, client=None, quiet=True):
    runtime = _NoSlotRuntime(ledger=None)
    if watcher is not None:
        runtime.required_block_rule = watcher
    coordinator = PlanningCoordinator(
        runtime=runtime, focus=object(), client=client or DummyClient()  # type: ignore[arg-type]
    )

    async def _dm(*, user_id):
        return "D1"

    monkeypatch.setattr(coordinator, "_resolve_dm_channel", _dm)

    async def _silences(*, user_id, at):
        return not quiet

    monkeypatch.setattr(coordinator, "_timeboxing_silences", _silences)

    seen: list[PlanningReminder] = []

    async def _card(reminder):
        seen.append(reminder)

    monkeypatch.setattr(coordinator, "_dispatch_planning_card", _card)
    return coordinator, seen


@pytest.mark.asyncio
async def test_a_reminder_the_recheck_finds_present_posts_nothing(monkeypatch):
    """The block came back between the nudge being scheduled and it firing."""
    client = DummyClient()
    watcher = _Watcher("present")
    coordinator, cards = _coordinator(monkeypatch, watcher=watcher, client=client)

    await coordinator.dispatch_planning_reminder(_reminder("closure"))

    assert client.posted == [] and cards == []
    assert [(u, s) for u, s, _ in watcher.asked] == [("U1", "closure")]


@pytest.mark.asyncio
async def test_a_reminder_whose_reason_still_holds_is_one_dm_line(monkeypatch):
    client = DummyClient()
    coordinator, cards = _coordinator(monkeypatch, watcher=_Watcher(REASON_MISSING), client=client)

    await coordinator.dispatch_planning_reminder(_reminder("closure"))

    assert len(client.posted) == 1
    assert client.posted[0]["text"] == "Your `closure` block is not on today's calendar."
    assert cards == []


@pytest.mark.asyncio
async def test_a_moved_out_planning_block_is_a_dm_line_and_never_the_card(monkeypatch):
    """The card books a planning block. A `moved_out` one exists already, so
    booking a second is exactly the wrong answer."""
    client = DummyClient()
    coordinator, cards = _coordinator(
        monkeypatch, watcher=_Watcher(REASON_MOVED_OUT), client=client
    )

    await coordinator.dispatch_planning_reminder(_reminder("planning", REASON_MOVED_OUT))

    assert len(client.posted) == 1 and cards == []


@pytest.mark.asyncio
async def test_a_reason_that_changed_is_dropped(monkeypatch):
    """Scheduled as `missing`, now `moved_out`: this rung's line is wrong, and
    the tick that judged it `moved_out` scheduled the right one."""
    client = DummyClient()
    coordinator, _ = _coordinator(monkeypatch, watcher=_Watcher(REASON_MOVED_OUT), client=client)

    await coordinator.dispatch_planning_reminder(_reminder("closure", REASON_MISSING))

    assert client.posted == []


@pytest.mark.asyncio
async def test_a_recheck_that_gives_no_verdict_drops_the_reminder(monkeypatch):
    """No verdict is not a confirmation, and the ladder is untouched anyway."""
    client = DummyClient()
    coordinator, _ = _coordinator(monkeypatch, watcher=_Watcher(None), client=client)

    await coordinator.dispatch_planning_reminder(_reminder("closure"))

    assert client.posted == []


@pytest.mark.asyncio
async def test_a_runtime_without_the_rule_posts_nothing_and_says_so(monkeypatch, caplog):
    client = DummyClient()
    coordinator, _ = _coordinator(monkeypatch, watcher=None, client=client)

    with caplog.at_level("WARNING"):
        await coordinator.dispatch_planning_reminder(_reminder("closure"))

    assert client.posted == []
    assert any("required_block" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_an_open_session_silences_the_reminder_before_the_recheck(monkeypatch):
    client = DummyClient()
    watcher = _Watcher(REASON_MISSING)
    coordinator, _ = _coordinator(monkeypatch, watcher=watcher, client=client, quiet=False)

    await coordinator.dispatch_planning_reminder(_reminder("closure"))

    assert client.posted == []
    assert watcher.asked == []


@pytest.mark.asyncio
async def test_the_planning_ladders_own_reminder_still_takes_the_card(monkeypatch):
    """The card path belongs to `PlanningSessionRule`, and is untouched."""
    coordinator, cards = _coordinator(monkeypatch, watcher=_Watcher("present"))

    await coordinator.dispatch_planning_reminder(
        PlanningReminder(scope="U1", kind="nudge1", attempt=1, message="m", user_id="U1")
    )

    assert len(cards) == 1
