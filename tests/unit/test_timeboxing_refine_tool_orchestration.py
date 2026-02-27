from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_agentchat")

import fateforger.agents.timeboxing.agent as timeboxing_agent_mod
from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.agent import Session
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from fateforger.agents.timeboxing.sync_engine import SyncOp, SyncOpType, SyncTransaction


def test_select_patch_instruction_returns_first_non_empty() -> None:
    """Patch orchestration should pick the first valid patch instruction."""
    patch = TimeboxingFlowAgent._select_patch_instruction(
        ["", "   ", "add lunch and buffer", "ignored second patch"]
    )
    assert patch == "add lunch and buffer"


def test_summarize_sync_transaction_reports_unchanged_when_no_ops() -> None:
    """Empty sync transactions should report unchanged calendar state."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    tx = SyncTransaction(status="committed")
    outcome = TimeboxingFlowAgent._summarize_sync_transaction(agent, tx)
    assert outcome.changed is False
    assert outcome.created == 0
    assert outcome.updated == 0
    assert outcome.deleted == 0
    assert outcome.failed == 0
    assert "unchanged" in outcome.note.lower()


def test_summarize_sync_transaction_reports_partial_counts() -> None:
    """Partial sync should include per-op success/failure counters."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    tx = SyncTransaction(
        ops=[
            SyncOp(
                op_type=SyncOpType.CREATE,
                gcal_event_id="fftb1",
                after_payload={},
            ),
            SyncOp(
                op_type=SyncOpType.UPDATE,
                gcal_event_id="fftb2",
                after_payload={},
            ),
        ],
        results=[{"ok": True}, {"ok": False}],
        status="partial",
    )
    outcome = TimeboxingFlowAgent._summarize_sync_transaction(agent, tx)
    assert outcome.status == "partial"
    assert outcome.changed is True
    assert outcome.created == 1
    assert outcome.updated == 0
    assert outcome.deleted == 0
    assert outcome.failed == 1
    assert "partially changed" in outcome.note.lower()


def test_memory_management_heuristic_detects_memory_only_requests() -> None:
    assert (
        TimeboxingFlowAgent._looks_like_memory_management_request(
            "show my saved constraints and preferences"
        )
        is True
    )
    assert (
        TimeboxingFlowAgent._looks_like_memory_management_request(
            "move deep work to 10:00 and update calendar"
        )
        is False
    )


@pytest.mark.asyncio
async def test_refine_tool_orchestration_tools_are_strict_schema_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 4 tool planner should not fail FunctionTool strict schema validation."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._model_client = object()

    session = Session(
        thread_ts="thread",
        channel_id="channel",
        user_id="user",
        stage=TimeboxingStage.REFINE,
        planned_date="2026-02-27",
        tz_name="Europe/Amsterdam",
    )

    class _SchemaCheckingAssistantAgent:
        def __init__(self, *args, **kwargs):
            _ = args
            for tool in kwargs.get("tools", []):
                # This is where strict schema validation explodes if defaults leak in.
                _ = tool.schema

        async def on_messages(self, _messages, _token):
            return SimpleNamespace(chat_message=SimpleNamespace(content="ok"))

    async def _fake_with_timeout(_label, awaitable, **_kwargs):
        return await awaitable

    monkeypatch.setattr(timeboxing_agent_mod, "AssistantAgent", _SchemaCheckingAssistantAgent)
    monkeypatch.setattr(timeboxing_agent_mod, "with_timeout", _fake_with_timeout)
    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_build_refine_memory_component",
        lambda self, *, session: object(),
    )
    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_queue_constraint_extraction",
        lambda self, **kwargs: None,
    )
    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_append_background_update_once",
        lambda self, _session, _text: None,
    )
    monkeypatch.setattr(
        TimeboxingFlowAgent,
        "_queue_reflection_memory_write",
        lambda self, **kwargs: None,
    )

    outcome = await TimeboxingFlowAgent._run_refine_tool_orchestration(
        agent,
        session=session,
        patch_message="Please shift deep work later by 30 minutes.",
        user_message="Show my saved constraints.",
    )

    assert outcome.calendar.status == "skipped"
