"""Integration tests for Slack timeboxing submit/undo button flow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_core import AgentId

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.messages import (
    TimeboxingCancelSubmit,
    TimeboxingConfirmSubmit,
    TimeboxingUndoSubmit,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from fateforger.agents.timeboxing.sync_engine import SyncOp, SyncOpType, SyncTransaction
from fateforger.agents.timeboxing.tb_models import TBPlan
from fateforger.agents.timeboxing.timebox import Timebox, timebox_to_tb_plan
from fateforger.slack_bot.constraint_review import encode_metadata
from fateforger.slack_bot.timeboxing_submit import (
    FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID,
    FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID,
    TimeboxSubmitActionPayload,
    TimeboxingSubmitCoordinator,
)


class _Ctx:
    topic_id = None
    sender = None


@dataclass
class _RecordedCall:
    """Captured runtime message dispatch record."""

    message: Any
    recipient: AgentId


class _Runtime:
    """Route coordinator messages directly into a real timeboxing agent session."""

    def __init__(self, *, agent: TimeboxingFlowAgent) -> None:
        self._agent = agent
        self.calls: list[_RecordedCall] = []

    async def send_message(self, message: Any, recipient: AgentId) -> Any:
        self.calls.append(_RecordedCall(message=message, recipient=recipient))
        if isinstance(message, TimeboxingConfirmSubmit):
            return await self._agent.on_confirm_submit(message, _Ctx())
        if isinstance(message, TimeboxingCancelSubmit):
            return await self._agent.on_cancel_submit(message, _Ctx())
        if isinstance(message, TimeboxingUndoSubmit):
            return await self._agent.on_undo_submit(message, _Ctx())
        raise AssertionError(f"Unexpected message type: {type(message)}")


class _Client:
    """Minimal Slack client stub for chat_update assertions."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def chat_update(self, **payload: Any) -> dict[str, Any]:
        self.updates.append(payload)
        return {"ok": True}


def _build_plan(*, summary: str = "Focus") -> TBPlan:
    """Create a deterministic plan fixture for submit/undo tests."""
    timebox = Timebox(
        events=[
            CalendarEvent(
                summary=summary,
                event_type=EventType.DEEP_WORK,
                start_time=time(9, 0),
                duration=timedelta(minutes=90),
            )
        ],
        date=date(2026, 2, 13),
        timezone="Europe/Amsterdam",
    )
    return timebox_to_tb_plan(timebox)


def _build_submit_tx() -> SyncTransaction:
    """Create a committed sync transaction for submit button tests."""
    return SyncTransaction(
        ops=[
            SyncOp(
                op_type=SyncOpType.CREATE,
                gcal_event_id="fftb123",
                after_payload={
                    "calendarId": "primary",
                    "eventId": "fftb123",
                    "summary": "Focus",
                    "start": "2026-02-13T09:00:00+01:00",
                    "end": "2026-02-13T10:30:00+01:00",
                },
            )
        ],
        status="committed",
    )


def _action_payload(*, action_id: str) -> TimeboxSubmitActionPayload:
    """Build a typed submit-button payload from a Slack-like action body."""
    meta = encode_metadata({"channel_id": "C1", "thread_ts": "T1", "user_id": "U1"})
    body = {
        "actions": [{"action_id": action_id, "value": meta}],
        "channel": {"id": "C1"},
        "message": {"ts": "M1"},
        "user": {"id": "U1"},
    }
    payload = TimeboxSubmitActionPayload.from_action_body(body)
    assert payload is not None
    return payload


def _build_agent_session() -> TimeboxingFlowAgent:
    """Return a minimally wired timeboxing agent with one active session."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    agent._calendar_submitter = SimpleNamespace(
        submit_plan=AsyncMock(return_value=_build_submit_tx()),
        undo_transaction=AsyncMock(return_value=SyncTransaction(status="undone")),
    )
    session = Session(thread_ts="T1", channel_id="C1", user_id="U1")
    session.tz_name = "Europe/Amsterdam"
    session.stage = TimeboxingStage.REVIEW_COMMIT
    session.pending_submit = True
    session.tb_plan = _build_plan()
    session.base_snapshot = _build_plan(summary="Base")
    agent._sessions["T1"] = session
    return agent


@pytest.mark.asyncio
async def test_confirm_button_submits_and_exposes_undo() -> None:
    """Confirm action should submit and update Slack with an Undo button."""
    agent = _build_agent_session()
    runtime = _Runtime(agent=agent)
    client = _Client()
    coordinator = TimeboxingSubmitCoordinator(runtime=runtime, client=client)

    await coordinator.handle_confirm_action(
        payload=_action_payload(action_id=FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID)
    )

    assert runtime.calls
    assert isinstance(runtime.calls[-1].message, TimeboxingConfirmSubmit)
    assert agent._sessions["T1"].pending_submit is False
    final_update = client.updates[-1]
    action_ids = [
        element.get("action_id")
        for block in final_update.get("blocks", [])
        for element in block.get("elements", [])
    ]
    assert FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID in action_ids


@pytest.mark.asyncio
async def test_undo_button_reverts_session_to_refine() -> None:
    """Undo action should restore session state and remove Undo button."""
    agent = _build_agent_session()
    agent._sessions["T1"].pending_submit = False
    agent._sessions["T1"].last_sync_transaction = _build_submit_tx()
    agent._sessions["T1"].last_sync_event_id_map = {"Base|09:00:00": "fftbbase"}
    agent._sessions["T1"].event_id_map = {"Focus|09:00:00": "fftb123"}

    runtime = _Runtime(agent=agent)
    client = _Client()
    coordinator = TimeboxingSubmitCoordinator(runtime=runtime, client=client)

    await coordinator.handle_undo_action(
        payload=_action_payload(action_id=FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID)
    )

    session = agent._sessions["T1"]
    assert session.stage == TimeboxingStage.REFINE
    assert session.last_sync_transaction is None
    final_update = client.updates[-1]
    action_ids = [
        element.get("action_id")
        for block in final_update.get("blocks", [])
        for element in block.get("elements", [])
    ]
    assert FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID not in action_ids


@pytest.mark.asyncio
async def test_undo_button_rejected_after_session_end() -> None:
    """Undo action should be rejected when session was already ended."""
    agent = _build_agent_session()
    agent._sessions["T1"].completed = True
    agent._sessions["T1"].last_sync_transaction = _build_submit_tx()

    runtime = _Runtime(agent=agent)
    client = _Client()
    coordinator = TimeboxingSubmitCoordinator(runtime=runtime, client=client)

    await coordinator.handle_undo_action(
        payload=_action_payload(action_id=FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID)
    )

    final_text = client.updates[-1]["text"]
    assert "already ended" in final_text
