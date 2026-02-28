"""Regression tests for GraphFlow DecisionNode session flags."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_core import CancellationToken

from fateforger.agents.timeboxing.agent import Session
from fateforger.agents.timeboxing.nodes.nodes import DecisionNode, TurnContext


class _OrchestratorStub:
    async def _decide_next_action(self, *_args, **_kwargs):
        raise AssertionError("Decision LLM path should not run when force rerun is set.")


@pytest.mark.asyncio
async def test_decision_node_respects_force_stage_rerun_flag() -> None:
    """DecisionNode should consume `force_stage_rerun` without touching LLM routing."""
    session = Session(thread_ts="T1", channel_id="C1", user_id="U1")
    session.force_stage_rerun = True
    turn_init = SimpleNamespace(turn=TurnContext(user_text="Proceed."))
    node = DecisionNode(
        orchestrator=_OrchestratorStub(),
        session=session,
        turn_init=turn_init,
    )

    await node.on_messages([], CancellationToken())

    assert turn_init.turn.decision is not None
    assert turn_init.turn.decision.action == "redo"
    assert turn_init.turn.decision.note == "stage_action_rerun"
    assert session.force_stage_rerun is False
