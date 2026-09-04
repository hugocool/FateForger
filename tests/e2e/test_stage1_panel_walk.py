"""Ten probe turns leave ten cards and one panel in the thread; a suspension
edits the panel in place, once, and never surfaces on a card's decided list
because `SUSPENDED_CONSTRAINT` is not a decided fact kind on this branch.

Driven through `_run_adaptive_timebox_turn`, the same seam
`tests/unit/test_stage_panel_in_the_turn.py` drives. The harness below is
copied from that file's `_wire` rather than imported: these are two test
files and sharing private helpers across them would couple their internals.
"""

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
        return {"ok": True, "ts": f"400.{len(self.posts)}"}

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


def _snapshot(revision: int, *, suspend: bool) -> PlanningSessionSnapshot:
    facts = [
        PlanningFact(
            fact_id=f"e{i}", kind=FactKind.REQUESTED_ACTIVITY, value=f"x{i}", source="user"
        )
        for i in range(revision)
    ]
    if suspend:
        facts.append(
            PlanningFact(
                fact_id=suspension_fact_id("c1"),
                kind=FactKind.SUSPENDED_CONSTRAINT,
                value={"uid": "c1", "reason": "not today", "note": None},
                source="user",
            )
        )
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=revision,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1
        ),
        applicable_constraints=[
            {
                "uid": "c1",
                "name": "r1",
                "necessity": "must",
                "anchors": [{"uid": "a", "name": "gym"}],
                "fade": None,
            },
            {"uid": "c2", "name": "r2", "necessity": "should", "anchors": [], "fade": 0.5},
        ],
        facts=facts,
    )


def _wire(monkeypatch, *, snapshots: list[PlanningSessionSnapshot]):
    """Same shape as `test_stage_panel_in_the_turn.py`'s `_wire`: the kernel
    is (re)built once per turn via `_timeboxing_kernel`, and the fake repo
    advances once per turn -- every *second* `load_or_create` call -- because
    `_run_adaptive_timebox_turn` loads once before the kernel runs and once
    after, and both reads within one turn must see that turn's snapshot."""

    outcome = AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="w")

    class Kernel:
        async def turn(self, request, progress):
            return outcome

    class Repo:
        def __init__(self) -> None:
            self._loads = 0

        async def load_or_create(self, key, owner_user_id):
            turn_index = min(self._loads // 2, len(snapshots) - 1)
            self._loads += 1
            return snapshots[turn_index]

    class Runtime:
        timeboxing_session_store = Repo()

    async def derive(*a, **k):
        return ProvidePlanningFacts(
            facts=[PlanningFact(fact_id="f", kind=FactKind.REQUESTED_ACTIVITY, value="x", source="user")]
        )

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", derive)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    registry = StageCardRegistry()
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    return Runtime(), registry


@pytest.mark.asyncio
async def test_ten_probes_one_panel_one_steer(monkeypatch) -> None:
    snapshots = [_snapshot(i + 1, suspend=(i >= 6)) for i in range(10)]
    runtime, registry = _wire(monkeypatch, snapshots=snapshots)
    client = _Client()

    cards = []
    for i in range(10):
        # A distinct `progress_ts` per turn, as real callers give each turn a
        # fresh "thinking" message: with one shared `progress_ts` for every
        # turn, `StageCardRegistry.transition` would see the same
        # (channel, ts) on every call and treat it as a redraw of the same
        # message rather than a transition, so it would never receipt the
        # previous card. A distinct ts per turn is what makes each card a new
        # message worth closing out the last one for.
        message = await handlers._run_adaptive_timebox_turn(
            runtime=runtime,
            client=client,
            logger=logging.getLogger(__name__),
            session_key="C1:1.0",
            actor_user_id="U1",
            interaction_id=f"i{i}",
            progress_channel="C1",
            progress_ts=f"1.{i}",
            card_channel="C1",
            card_thread_ts="T1",
            user_text=f"probe {i}",
        )
        cards.append(message)

    # Ten cards, each stamped with the Stage 1 header.
    assert len(cards) == 10
    for card in cards:
        assert card.blocks[0]["text"]["text"].startswith("*1/5 · Constraints*")

    # Exactly one panel post, landing after the first turn's card.
    panels = [
        p for p in client.posts if p.get("blocks", [{}])[0].get("block_id") == "ff_timebox_context_panel"
    ]
    assert len(panels) == 1

    panel_shown = registry.panel_shown("C1:1.0")
    assert panel_shown is not None
    panel_ts = panel_shown.ts

    # Exactly one panel edit: the turn (the 7th, index 6) whose snapshot
    # first carries the suspension fact. Rows and the panel's own `day` are
    # constant across every other turn, so `sync_panel` has nothing else to
    # redraw for.
    panel_edits = [u for u in client.updates if u["ts"] == panel_ts]
    assert len(panel_edits) == 1
    assert "not today" in panel_edits[0]["blocks"][1]["elements"][0]["text"]

    # `SUSPENDED_CONSTRAINT` is not in `stage_cards._FACT_LABELS` on this
    # branch, so the decided list never names a session suspension -- the
    # walk does not assert on it here.
