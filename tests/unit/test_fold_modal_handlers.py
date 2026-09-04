"""A press on Show rules opens the fold from durable state; an overflow pick
inside it takes the same path as a card button and refreshes the modal."""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import PlanningDay, PlanningSessionSnapshot
from fateforger.slack_bot.stage_card_registry import StageCardRegistry
from fateforger.slack_bot.stage_context import context_panel
from fateforger.slack_bot.timeboxing_cards import FF_TIMEBOX_STEER_ACTION_ID, artifact_action_value


class _Client:
    def __init__(self) -> None:
        self.opened: list[dict] = []
        self.updated: list[dict] = []
        self.ephemeral: list[dict] = []

    async def views_open(self, **payload):
        self.opened.append(dict(payload))
        return {"ok": True}

    async def views_update(self, **payload):
        self.updated.append(dict(payload))
        return {"ok": True}

    async def chat_postEphemeral(self, **payload):
        self.ephemeral.append(dict(payload))
        return {"ok": True}


def _snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0", revision=5, owner_user_id="U1",
        planning_day=PlanningDay.lock_default(value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1),
        applicable_constraints=[{"uid": "c1", "name": "r", "necessity": "must", "anchors": [], "fade": None}],
    )


def _runtime(snapshot):
    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return snapshot

    class Runtime:
        timeboxing_session_store = Repo()

    return Runtime()


def _press_body(value: str, *, in_view: bool = False) -> dict:
    body = {
        "trigger_id": "T1",
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "message": {"ts": "1.0"},
        "actions": [{"action_id": FF_TIMEBOX_STEER_ACTION_ID, "selected_option": {"value": value}, "value": None}],
    }
    if in_view:
        body["view"] = {"id": "V1", "private_metadata": artifact_action_value(session_key="C1:1.0", expected_revision=5, decision="advance", artifact=None)}
        body["container"] = {"type": "view", "view_id": "V1"}
        body.pop("channel")
        body.pop("message")
    return body


@pytest.mark.asyncio
async def test_show_rules_opens_the_fold_from_the_snapshot() -> None:
    client = _Client()
    value = artifact_action_value(session_key="C1:1.0", expected_revision=5, decision="advance", artifact=None)
    body = {"trigger_id": "T1", "user": {"id": "U1"}, "channel": {"id": "C1"}, "message": {"ts": "1.0"},
            "actions": [{"action_id": "ff_timebox_show_rules", "value": value}]}
    await handlers._handle_show_rules(_runtime(_snapshot()), client, logging.getLogger(__name__), body=body)
    (opened,) = client.opened
    assert opened["trigger_id"] == "T1"
    assert opened["view"]["type"] == "modal"
    assert any(b.get("accessory", {}).get("type") == "overflow" for b in opened["view"]["blocks"])


@pytest.mark.asyncio
async def test_a_failed_open_tells_the_user_to_press_again() -> None:
    client = _Client()

    async def boom(**payload):
        raise RuntimeError("expired trigger")

    client.views_open = boom
    value = artifact_action_value(session_key="C1:1.0", expected_revision=5, decision="advance", artifact=None)
    body = {"trigger_id": "T1", "user": {"id": "U1"}, "channel": {"id": "C1"}, "message": {"ts": "1.0"},
            "actions": [{"action_id": "ff_timebox_show_rules", "value": value}]}
    await handlers._handle_show_rules(_runtime(_snapshot()), client, logging.getLogger(__name__), body=body)
    assert len(client.ephemeral) == 1


@pytest.mark.asyncio
async def test_a_fold_pick_reaches_the_artifact_action_path_and_refreshes_the_modal(monkeypatch) -> None:
    client = _Client()
    seen: list[str] = []

    async def fake_handle(*, runtime, client, logger, value, channel_id, thread_ts, actor_user_id, interaction_id):
        seen.append(json.loads(value)["decision"])

    monkeypatch.setattr(handlers, "_handle_timebox_artifact_action", fake_handle)
    registry = StageCardRegistry()
    registry.remember_panel(
        "C1:1.0", channel="C1", ts="9.0", thread_ts="1.0",
        panel=context_panel(_snapshot(), None),
    )
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    value = json.dumps({"schema_version": 1, "session_key": "C1:1.0", "expected_revision": 5,
                        "decision": "steer_not_today", "constraint_uid": "c1"})
    await handlers._handle_fold_pick(_runtime(_snapshot()), client, logging.getLogger(__name__),
                                     body=_press_body(value, in_view=True))
    assert seen == ["steer_not_today"]
    (updated,) = client.updated
    assert updated["view_id"] == "V1"
    assert updated["view"]["type"] == "modal"


@pytest.mark.asyncio
async def test_a_fold_pick_in_a_dm_session_delivers_with_the_dm_sentinel(monkeypatch) -> None:
    """`ShownPanel.thread_ts` is None on the DM route (no thread to post
    into there); every other turn on that route uses the `"dm"` sentinel for
    exactly that case, so a fold pick passes it onward too rather than
    threading the reply under the panel message."""
    client = _Client()
    seen: dict = {}

    async def fake_handle(*, runtime, client, logger, value, channel_id, thread_ts, actor_user_id, interaction_id):
        seen["thread_ts"] = thread_ts

    monkeypatch.setattr(handlers, "_handle_timebox_artifact_action", fake_handle)
    registry = StageCardRegistry()
    registry.remember_panel(
        "C1:1.0", channel="D1", ts="42.0", thread_ts=None,
        panel=context_panel(_snapshot(), None),
    )
    monkeypatch.setattr(handlers, "_stage_cards", registry)
    value = json.dumps({"schema_version": 1, "session_key": "C1:1.0", "expected_revision": 5,
                        "decision": "steer_not_today", "constraint_uid": "c1"})
    await handlers._handle_fold_pick(_runtime(_snapshot()), client, logging.getLogger(__name__),
                                     body=_press_body(value, in_view=True))
    assert seen["thread_ts"] == "dm"


@pytest.mark.asyncio
async def test_a_fold_pick_after_the_session_ended_redraws_the_modal_closed(monkeypatch) -> None:
    """`retire_panel` drops the panel record on Cancelled/Committed, but a
    modal opened earlier still shows live menus. A pick then finds no panel
    and a view press has no channel for an ephemeral, so the modal itself is
    redrawn to say the session is over."""
    client = _Client()
    called: list[str] = []

    async def fake_handle(*, runtime, client, logger, value, channel_id, thread_ts, actor_user_id, interaction_id):
        called.append(value)

    monkeypatch.setattr(handlers, "_handle_timebox_artifact_action", fake_handle)
    monkeypatch.setattr(handlers, "_stage_cards", StageCardRegistry())
    value = json.dumps({"schema_version": 1, "session_key": "C1:1.0", "expected_revision": 5,
                        "decision": "steer_not_today", "constraint_uid": "c1"})
    await handlers._handle_fold_pick(_runtime(_snapshot()), client, logging.getLogger(__name__),
                                     body=_press_body(value, in_view=True))
    assert called == []
    (updated,) = client.updated
    assert updated["view_id"] == "V1"
    assert not any(
        b.get("accessory", {}).get("type") == "overflow" for b in updated["view"]["blocks"]
    )
