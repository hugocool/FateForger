from __future__ import annotations

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.sync_engine import SyncOp, SyncOpType, SyncTransaction


def test_select_refine_tool_intents_prioritizes_patch() -> None:
    """Patch-critical intent should be selected ahead of memory intents."""
    patch, memory = TimeboxingFlowAgent._select_refine_tool_intents(
        [
            (10, "memory", "remember this preference"),
            (0, "patch", "add lunch and buffer"),
            (0, "patch", "ignored second patch"),
        ]
    )
    assert patch == "add lunch and buffer"
    assert memory == "remember this preference"


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
