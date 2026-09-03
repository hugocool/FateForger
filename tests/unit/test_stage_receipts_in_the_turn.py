"""Driven through `_run_adaptive_timebox_turn`, because the bug was in the
wiring: every renderer worked and no card was ever closed (#265)."""

from __future__ import annotations

import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactKind,
    AwaitingApproval,
    Cancelled,
    CancelSession,
    ConfirmPlanningDay,
    GoBack,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
    TurnFailed,
)
from fateforger.slack_bot.stage_card_registry import StageCardRegistry
from fateforger.slack_bot.timebox_candidate import (
    PendingTimeboxCandidates,
    ValidatedTimeboxCandidate,
)
from fateforger.slack_bot.timeboxing_cards import (
    FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
    TIMEBOX_TURN_FAILED_TEXT,
)


class _Client:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def chat_update(self, **payload):
        self.updates.append(dict(payload))
        return {"ok": True}


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 3), timezone="Europe/Amsterdam", lock_revision=1
    )


def _skeleton() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"markdown": "# Morning\n- memo", "reasoning": "memo first"},
        dependency_revisions={"planning_day": 1},
    )


def _wire(monkeypatch, *, outcome, intent, snapshot: PlanningSessionSnapshot):
    class Kernel:
        async def turn(self, request, progress):
            return outcome

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return snapshot

    class Runtime:
        timeboxing_session_store = Repo()

    async def derive(*a, **k):
        return intent

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", derive)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    registry = StageCardRegistry()
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    return Runtime(), registry


async def _turn(runtime, client, *, ts: str, user_text: str = "go on"):
    return await handlers._run_adaptive_timebox_turn(
        runtime=runtime,
        client=client,
        logger=logging.getLogger(__name__),
        session_key="C1:1.0",
        actor_user_id="U1",
        interaction_id=f"i-{ts}",
        progress_channel="C1",
        progress_ts=ts,
        card_channel="C1",
        card_thread_ts="1.0",
        user_text=user_text,
    )


def _action_ids(blocks) -> set[str]:
    return {
        e["action_id"]
        for b in blocks or []
        if b.get("type") == "actions"
        for e in b.get("elements", [])
        if "action_id" in e
    }


@pytest.mark.asyncio
async def test_the_next_card_turns_the_previous_one_into_a_receipt(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=Advance(),
        snapshot=snapshot,
    )
    client = _Client()

    first = await _turn(runtime, client, ts="100.1")
    assert FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID in _action_ids(first.blocks)
    assert registry.shown("C1:1.0").ts == "100.1"

    await _turn(runtime, client, ts="100.2")

    receipts = [u for u in client.updates if u.get("ts") == "100.1"]
    assert len(receipts) == 1
    assert _action_ids(receipts[0]["blocks"]) == set()
    assert "✅ confirmed" in receipts[0]["blocks"][0]["text"]["text"]
    assert registry.shown("C1:1.0").ts == "100.2"


@pytest.mark.asyncio
async def test_going_back_labels_the_receipt_as_reopened(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=GoBack(),
        snapshot=snapshot,
    )
    client = _Client()
    await _turn(runtime, client, ts="100.1")
    await _turn(runtime, client, ts="100.2")

    receipt = next(u for u in client.updates if u.get("ts") == "100.1")
    assert "↩️ reopened" in receipt["blocks"][0]["text"]["text"]


@pytest.mark.asyncio
async def test_a_failed_turn_leaves_the_previous_card_live(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=Advance(),
        snapshot=snapshot,
    )
    client = _Client()
    await _turn(runtime, client, ts="100.1")

    _wire(monkeypatch, outcome=TurnFailed(code="x", message="x"), intent=Advance(), snapshot=snapshot)
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    await _turn(runtime, client, ts="100.2")

    assert [u for u in client.updates if u.get("ts") == "100.1"] == []
    assert registry.shown("C1:1.0").ts == "100.1"


@pytest.mark.asyncio
async def test_a_typed_day_change_relabels_the_thread_root(monkeypatch) -> None:
    """#265: the button path relabelled the root, the typed path never did."""
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=2, owner_user_id="U1", planning_day=_day()
    )
    runtime, _registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=ConfirmPlanningDay(planning_day=_day()),
        snapshot=snapshot,
    )
    client = _Client()

    await _turn(runtime, client, ts="100.1", user_text="actually plan Friday")

    root_writes = [u for u in client.updates if u.get("ts") == "1.0"]
    assert len(root_writes) == 1
    assert "Timeboxing session for" in root_writes[0]["text"]
    assert "blocks" not in root_writes[0]


# -- The armed commit button outlives the card that drew it --------------------
# `_pending_candidates` is what the commit gate actually spends. Back off stage
# 4 drops the `validated_candidate` artifact, but nothing cleared the armed
# candidate: the receipt edit that neutralises the stale Commit button is
# best-effort and swallowed, and a press on a button that survived it reaches
# `_handle_timebox_candidate_approval`, finds no candidate artifact, returns
# False, falls through to `_execute_harness_approval` -- which consumes the
# still-armed candidate and commits the plan the user just went Back from.


def _armed(monkeypatch) -> PendingTimeboxCandidates:
    pending = PendingTimeboxCandidates()
    pending.replace(
        "C1:1.0",
        ValidatedTimeboxCandidate(
            digest="d" * 64,
            snapshot={"calendar_id": "cal", "day": "2026-09-03"},
            patch={"ops": []},
            rendered="09:00 memo",
        ),
        owner_user_id="U1",
    )
    monkeypatch.setattr(handlers, "_pending_candidates", pending)
    return pending


@pytest.mark.asyncio
async def test_back_off_the_candidate_disarms_the_commit_button(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, _registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=GoBack(),
        snapshot=snapshot,
    )
    pending = _armed(monkeypatch)

    await _turn(runtime, _Client(), ts="100.1")

    assert pending.peek("C1:1.0") is None


@pytest.mark.asyncio
async def test_cancelling_disarms_the_commit_button(monkeypatch) -> None:
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_day(),
        status="cancelled",
    )
    runtime, _registry = _wire(
        monkeypatch,
        outcome=Cancelled(),
        intent=CancelSession(),
        snapshot=snapshot,
    )
    pending = _armed(monkeypatch)

    await _turn(runtime, _Client(), ts="100.1")

    assert pending.peek("C1:1.0") is None


@pytest.mark.asyncio
async def test_the_candidate_on_offer_stays_armed(monkeypatch) -> None:
    """The disarm is bound to what the session holds, not to which intent ran:
    a turn that re-presents the candidate must leave a spendable one behind, or
    the Commit button it just drew answers nothing."""
    candidate = PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload={
            "digest": "d" * 64,
            "snapshot": {"calendar_id": "cal", "day": "2026-09-03"},
            "patch": {"ops": []},
            "rendered": "09:00 memo",
        },
        dependency_revisions={"skeleton": 1},
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_day(),
        artifacts=[candidate],
    )
    runtime, _registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=candidate),
        intent=Advance(),
        snapshot=snapshot,
    )
    pending = _armed(monkeypatch)

    await _turn(runtime, _Client(), ts="100.1")

    assert pending.peek("C1:1.0") is not None


@pytest.mark.asyncio
async def test_a_skeleton_older_than_its_contract_fails_the_turn(
    monkeypatch, caplog
) -> None:
    """The mapper refuses a stored payload that is not a `SkeletonPayload`
    (#267: `{"blocks": []}`, the 2026-09-02 shape, which used to be drawn as an
    empty day). The turn itself is already saved, so only its picture failed:
    the user gets the one stable failure sentence, and the card they are
    standing on stays live rather than being receipted over as confirmed.
    """
    stale = PlanningArtifact.create(
        artifact_id="skeleton-old",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload={"blocks": []},
        dependency_revisions={"planning_day": 1},
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0", revision=3, owner_user_id="U1", planning_day=_day()
    )
    runtime, registry = _wire(
        monkeypatch,
        outcome=AwaitingApproval(artifact=_skeleton()),
        intent=Advance(),
        snapshot=snapshot,
    )
    client = _Client()
    await _turn(runtime, client, ts="100.1")

    _wire(monkeypatch, outcome=AwaitingApproval(artifact=stale), intent=Advance(), snapshot=snapshot)
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    with caplog.at_level(logging.ERROR):
        message = await _turn(runtime, client, ts="100.2")

    assert message.text == TIMEBOX_TURN_FAILED_TEXT
    assert [u for u in client.updates if u.get("ts") == "100.1"] == []
    assert registry.shown("C1:1.0").ts == "100.1"
