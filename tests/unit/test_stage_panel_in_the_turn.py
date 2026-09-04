"""Driven through `_run_adaptive_timebox_turn`, like the receipt tests: the
panel is posted once, above the first card, and edited when the rows change."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    AwaitingUser,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    suspension_fact_id,
)
from fateforger.slack_bot.stage_card_registry import StageCardRegistry


class _Client:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posts.append(dict(payload))
        return {"ok": True, "ts": f"300.{len(self.posts)}"}

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


def _snapshot(suspend: list[str] = ()) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=5,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1),
        applicable_constraints=[{"uid": "c1", "name": "r", "necessity": "must", "anchors": [], "fade": None}],
        facts=[
            PlanningFact(fact_id=suspension_fact_id(u), kind=FactKind.SUSPENDED_CONSTRAINT,
                         value={"uid": u, "reason": "not today"}, source="user")
            for u in suspend
        ],
    )


def _wire(monkeypatch, *, snapshots: list[PlanningSessionSnapshot]):
    outcome = AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="w")

    class Kernel:
        async def turn(self, request, progress):
            return outcome

    class Repo:
        """`_run_adaptive_timebox_turn` loads twice per turn (once before the
        kernel runs, once after, to get the post-turn `current`). Advancing on
        every call would hand each turn a different snapshot for its "before"
        and "after" reads; advancing once per turn -- every second call --
        gives both reads the snapshot that turn intends, regardless of how
        many loads a turn happens to make."""

        def __init__(self) -> None:
            self._loads = 0

        async def load_or_create(self, key, owner_user_id):
            turn_index = min(self._loads // 2, len(snapshots) - 1)
            self._loads += 1
            return snapshots[turn_index]

    class Runtime:
        timeboxing_session_store = Repo()

    async def derive(*a, **k):
        return ProvidePlanningFacts(facts=[PlanningFact(fact_id="f", kind=FactKind.REQUESTED_ACTIVITY, value="x", source="user")])

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", derive)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    registry = StageCardRegistry()
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    return Runtime(), registry


@pytest.mark.asyncio
async def test_the_panel_is_posted_once_above_the_first_card_then_edited(monkeypatch) -> None:
    runtime, registry = _wire(monkeypatch, snapshots=[_snapshot(), _snapshot(suspend=["c1"])])
    client = _Client()
    kwargs = dict(
        runtime=runtime,
        client=client,
        logger=logging.getLogger(__name__),
        session_key="C1:1.0",
        actor_user_id="U1",
        progress_channel="C1",
        progress_ts="100.1",
        card_channel="C1",
        card_thread_ts="1.0",
    )
    await handlers._run_adaptive_timebox_turn(interaction_id="i1", user_text="gym at 18", **kwargs)
    assert registry.panel_shown("C1:1.0") is not None
    first_panel_ts = registry.panel_shown("C1:1.0").ts
    await handlers._run_adaptive_timebox_turn(interaction_id="i2", user_text="not today for that", **kwargs)
    assert registry.panel_shown("C1:1.0").ts == first_panel_ts
    assert any(u["ts"] == first_panel_ts for u in client.updates)
