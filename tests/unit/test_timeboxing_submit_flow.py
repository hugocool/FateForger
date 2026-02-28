"""Unit tests for Stage 5 submit / cancel / undo session transitions."""

from __future__ import annotations

from datetime import date, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

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
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.timeboxing_submit import FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID


class _Ctx:
    topic_id = None
    sender = None


def _build_plan(*, summary: str = "Focus") -> TBPlan:
    """Return a minimal TBPlan for tests."""
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


def _build_submit_transaction() -> SyncTransaction:
    """Return a committed create transaction for test assertions."""
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


@pytest.mark.asyncio
async def test_confirm_submit_updates_session_and_returns_undo_button() -> None:
    """Confirm submit should persist transaction state and include an Undo button."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    agent._calendar_submitter = SimpleNamespace(
        submit_plan=AsyncMock(return_value=_build_submit_transaction()),
    )

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.tz_name = "Europe/Amsterdam"
    session.stage = TimeboxingStage.REVIEW_COMMIT
    session.pending_submit = True
    session.tb_plan = _build_plan()
    session.base_snapshot = _build_plan(summary="Base")
    session.event_id_map = {}
    agent._sessions["t1"] = session

    result = await agent.on_confirm_submit(
        TimeboxingConfirmSubmit(channel_id="c1", thread_ts="t1", user_id="u1"),
        _Ctx(),
    )

    assert isinstance(result, SlackBlockMessage)
    assert session.pending_submit is False
    assert session.last_sync_transaction is not None
    assert session.last_sync_event_id_map == {}
    assert "Focus|09:00:00" in session.event_id_map
    action_ids = [
        element.get("action_id")
        for block in result.blocks
        for element in block.get("elements", [])
    ]
    assert FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID in action_ids


@pytest.mark.asyncio
async def test_confirm_submit_refreshes_remote_baseline_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 5 submit should refresh remote baseline once after sync."""
    submit_plan = AsyncMock(return_value=_build_submit_transaction())
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    agent._calendar_submitter = SimpleNamespace(submit_plan=submit_plan)

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.tz_name = "Europe/Amsterdam"
    session.stage = TimeboxingStage.REVIEW_COMMIT
    session.pending_submit = True
    session.tb_plan = _build_plan()
    session.base_snapshot = _build_plan(summary="Base")
    session.event_id_map = {}
    session.remote_event_ids_by_index = []
    agent._sessions["t1"] = session

    refreshed = {"called": 0}

    async def _fake_refresh(self: TimeboxingFlowAgent, target: Session) -> None:
        _ = self
        refreshed["called"] += 1
        target.base_snapshot = _build_plan(summary=f"Remote-{refreshed['called']}")
        target.remote_event_ids_by_index = ["fftb123"]

    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_refresh_remote_baseline_after_sync",
        _fake_refresh,
    )

    result = await agent.on_confirm_submit(
        TimeboxingConfirmSubmit(channel_id="c1", thread_ts="t1", user_id="u1"),
        _Ctx(),
    )

    assert isinstance(result, SlackBlockMessage)
    assert refreshed["called"] == 1
    assert submit_plan.await_count == 1
    assert submit_plan.await_args.kwargs["remote"].events[0].n == "Base"
    assert session.base_snapshot is not None
    assert session.base_snapshot.events[0].n == "Remote-1"
    assert session.remote_event_ids_by_index == ["fftb123"]


@pytest.mark.asyncio
async def test_cancel_submit_returns_to_refine() -> None:
    """Cancel submit should clear pending state and move session to refine."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.tz_name = "Europe/Amsterdam"
    session.stage = TimeboxingStage.REVIEW_COMMIT
    session.pending_submit = True
    agent._sessions["t1"] = session

    result = await agent.on_cancel_submit(
        TimeboxingCancelSubmit(channel_id="c1", thread_ts="t1", user_id="u1"),
        _Ctx(),
    )

    assert isinstance(result, TextMessage)
    assert session.pending_submit is False
    assert session.stage == TimeboxingStage.REFINE


@pytest.mark.asyncio
async def test_undo_submit_restores_snapshot_and_refine_stage() -> None:
    """Undo should restore base snapshot, clear undo state, and return to refine."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    submit_tx = _build_submit_transaction()
    agent._calendar_submitter = SimpleNamespace(
        undo_transaction=AsyncMock(return_value=SyncTransaction(status="undone")),
    )

    base_snapshot = _build_plan(summary="Base")
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.tz_name = "Europe/Amsterdam"
    session.stage = TimeboxingStage.REVIEW_COMMIT
    session.pending_submit = False
    session.tb_plan = _build_plan(summary="Edited")
    session.base_snapshot = base_snapshot
    session.last_sync_transaction = submit_tx
    session.last_sync_event_id_map = {"Base|09:00:00": "fftbbase"}
    session.event_id_map = {"Focus|09:00:00": "fftb123"}
    agent._sessions["t1"] = session

    result = await agent.on_undo_submit(
        TimeboxingUndoSubmit(channel_id="c1", thread_ts="t1", user_id="u1"),
        _Ctx(),
    )

    assert isinstance(result, SlackBlockMessage)
    assert session.stage == TimeboxingStage.REFINE
    assert session.last_sync_transaction is None
    assert session.last_sync_event_id_map is None
    assert session.event_id_map == {"Base|09:00:00": "fftbbase"}
    assert session.tb_plan is not None
    assert session.tb_plan.model_dump() == base_snapshot.model_dump()


@pytest.mark.asyncio
async def test_undo_submit_rejected_when_session_ended() -> None:
    """Undo should be rejected when the session is already marked ended."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._sessions = {}
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.tz_name = "Europe/Amsterdam"
    session.completed = True
    session.last_sync_transaction = _build_submit_transaction()
    agent._sessions["t1"] = session

    result = await agent.on_undo_submit(
        TimeboxingUndoSubmit(channel_id="c1", thread_ts="t1", user_id="u1"),
        _Ctx(),
    )

    assert isinstance(result, TextMessage)
    assert "already ended" in result.content


@pytest.mark.asyncio
async def test_submit_current_plan_refreshes_remote_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 4 submit should refresh remote baseline once after sync."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    submit_plan = AsyncMock(return_value=_build_submit_transaction())
    agent._calendar_submitter = SimpleNamespace(submit_plan=submit_plan)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.tz_name = "Europe/Amsterdam"
    session.tb_plan = _build_plan(summary="Edited")
    session.base_snapshot = _build_plan(summary="Base")
    session.event_id_map = {}
    session.remote_event_ids_by_index = []

    refreshed = {"called": 0}

    async def _fake_refresh(self: TimeboxingFlowAgent, target: Session) -> None:
        _ = self
        refreshed["called"] += 1
        target.base_snapshot = _build_plan(summary=f"Remote-{refreshed['called']}")
        target.remote_event_ids_by_index = ["fftb123"]

    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_refresh_remote_baseline_after_sync",
        _fake_refresh,
    )

    outcome = await TimeboxingFlowAgent._submit_current_plan(agent, session)

    assert refreshed["called"] == 1
    assert submit_plan.await_count == 1
    assert submit_plan.await_args.kwargs["remote"].events[0].n == "Base"
    assert outcome.status == "committed"
    assert session.base_snapshot is not None
    assert session.base_snapshot.events[0].n == "Remote-1"
    assert session.remote_event_ids_by_index == ["fftb123"]
