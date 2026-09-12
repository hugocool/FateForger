"""Driven through `_run_adaptive_timebox_turn`, like the receipt tests: the
panel is posted once, on the first row-carrying turn, right after that turn's
card, and edited in place on every later turn."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingUser,
    Cancelled,
    Committed,
    FactKind,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    suspension_fact_id,
)
from fateforger.slack_bot.stage_card_registry import StageCardRegistry


def _receipt() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"tx_id": "tx-A"},
        dependency_revisions={},
    )


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


def _snapshot(suspend: list[str] = (), *, status: str = "open") -> PlanningSessionSnapshot:
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
        status=status,
    )


def _wire(
    monkeypatch,
    *,
    snapshots: list[PlanningSessionSnapshot],
    outcomes: list | None = None,
):
    if outcomes is None:
        default = AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="w")
        outcomes = [default] * len(snapshots)

    class Kernel:
        def __init__(self, outcome) -> None:
            self._outcome = outcome

        async def turn(self, request, progress):
            return self._outcome

    # `_timeboxing_kernel(...)` is called once per turn (a fresh instance
    # each time), so the outcome advances on that call, not on `Kernel.turn`
    # -- the same "once per turn" shape as `Repo.load` below.
    kernel_calls = {"n": 0}

    def make_kernel(*_a, **_k):
        turn_index = min(kernel_calls["n"], len(outcomes) - 1)
        kernel_calls["n"] += 1
        return Kernel(outcomes[turn_index])

    class Repo:
        """`_run_adaptive_timebox_turn` loads twice per turn (once before the
        kernel runs, once after, to get the post-turn `current`). Advancing on
        every call would hand each turn a different snapshot for its "before"
        and "after" reads; advancing once per turn -- every second call --
        gives both reads the snapshot that turn intends, regardless of how
        many loads a turn happens to make."""

        def __init__(self) -> None:
            self._loads = 0

        async def load(self, key):
            turn_index = min(self._loads // 2, len(snapshots) - 1)
            self._loads += 1
            return snapshots[turn_index]

    class Runtime:
        timeboxing_session_store = Repo()

    async def derive(*a, **k):
        return ProvidePlanningFacts(facts=[PlanningFact(fact_id="f", kind=FactKind.REQUESTED_ACTIVITY, value="x", source="user")])

    monkeypatch.setattr(handlers, "_timeboxing_kernel", make_kernel)
    monkeypatch.setattr(handlers, "derive_timebox_intent", derive)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    registry = StageCardRegistry()
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    return Runtime(), registry


@pytest.mark.asyncio
async def test_the_panel_is_posted_once_then_edited_in_place(monkeypatch) -> None:
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


@pytest.mark.asyncio
async def test_a_cancelled_turn_retires_the_panel(monkeypatch) -> None:
    runtime, registry = _wire(
        monkeypatch,
        snapshots=[_snapshot(), _snapshot(status="cancelled")],
        outcomes=[
            AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="w"),
            Cancelled(),
        ],
    )
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
    panel_ts = registry.panel_shown("C1:1.0").ts

    await handlers._run_adaptive_timebox_turn(interaction_id="i2", user_text="cancel", **kwargs)

    assert registry.panel_shown("C1:1.0") is None
    retirement = next(u for u in client.updates if u["ts"] == panel_ts)
    assert "cancelled" in retirement["blocks"][0]["text"]["text"]
    assert "accessory" not in retirement["blocks"][0]


@pytest.mark.asyncio
async def test_a_committed_turn_retires_the_panel(monkeypatch) -> None:
    runtime, registry = _wire(
        monkeypatch,
        snapshots=[_snapshot(), _snapshot(status="committed")],
        outcomes=[
            AwaitingUser(requirement_id="elicit.body.unclear", question="q", why_needed="w"),
            Committed(receipt=_receipt()),
        ],
    )
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
    panel_ts = registry.panel_shown("C1:1.0").ts

    await handlers._run_adaptive_timebox_turn(interaction_id="i2", user_text="commit", **kwargs)

    assert registry.panel_shown("C1:1.0") is None
    retirement = next(u for u in client.updates if u["ts"] == panel_ts)
    assert "committed" in retirement["blocks"][0]["text"]["text"]
    assert "accessory" not in retirement["blocks"][0]


@pytest.mark.asyncio
async def test_a_dm_turn_posts_the_panel_top_level(monkeypatch) -> None:
    runtime, registry = _wire(monkeypatch, snapshots=[_snapshot()])
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
        card_thread_ts="dm",
    )
    await handlers._run_adaptive_timebox_turn(interaction_id="i1", user_text="gym at 18", **kwargs)

    panel_posts = [p for p in client.posts if p["blocks"][0].get("block_id") == "ff_timebox_context_panel"]
    assert len(panel_posts) == 1
    assert "thread_ts" not in panel_posts[0]
    assert registry.panel_shown("C1:1.0").thread_ts is None
