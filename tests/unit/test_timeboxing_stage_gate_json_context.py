"""Tests for stage-gate input wiring (TOON list injection)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.stage_gating import StageGateOutput, TimeboxingStage


@dataclass
class _DummyChatMessage:
    content: Any


@dataclass
class _DummyResponse:
    chat_message: _DummyChatMessage


class _CapturingStageAgent:
    """Fake stage agent that captures incoming messages and returns a fixed output."""

    def __init__(self) -> None:
        self.last_messages: list[TextMessage] = []

    async def on_messages(self, messages: list[TextMessage], _token: Any) -> _DummyResponse:
        """Capture messages and return a minimal StageGateOutput."""
        self.last_messages = messages
        return _DummyResponse(
            chat_message=_DummyChatMessage(
                content=StageGateOutput(
                    stage_id=TimeboxingStage.COLLECT_CONSTRAINTS,
                    ready=False,
                    summary=["ok"],
                    missing=["x"],
                    question="q",
                    facts={},
                )
            )
        )


class _MalformedStageAgent:
    """Fake stage agent returning malformed payload to test fallback behavior."""

    async def on_messages(self, messages: list[TextMessage], _token: Any) -> _DummyResponse:
        _ = messages
        return _DummyResponse(chat_message=_DummyChatMessage(content="not-json"))


@pytest.mark.asyncio
async def test_run_stage_gate_sends_strict_json_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensures `_run_stage_gate` injects list-shaped data via TOON tables."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _noop_ensure_stage_agents(self: TimeboxingFlowAgent) -> None:
        """Avoid building real LLM agents in this unit test."""
        return None

    monkeypatch.setattr(TimeboxingFlowAgent, "_ensure_stage_agents", _noop_ensure_stage_agents)

    capturing = _CapturingStageAgent()
    agent._stage_agents = {TimeboxingStage.COLLECT_CONSTRAINTS: capturing}  # type: ignore[attr-defined]

    context = {
        "stage_id": "CollectConstraints",
        "user_message": "hi",
        "facts": {"k": 1},
        "durable_constraints": [
            {
                "name": "Sleep target",
                "description": "Aim for 8 hours",
                "necessity": "should",
                "status": "proposed",
                "source": "system",
                "scope": "profile",
                "tags": [],
                "hints": {},
            }
        ],
    }
    out = await TimeboxingFlowAgent._run_stage_gate(  # type: ignore[misc]
        agent,
        stage=TimeboxingStage.COLLECT_CONSTRAINTS,
        user_message="hi",
        context=context,
    )

    assert out.stage_id == TimeboxingStage.COLLECT_CONSTRAINTS
    assert capturing.last_messages, "Expected a single JSON message to be sent"
    content = capturing.last_messages[0].content
    assert "TOON format" in content
    assert "facts_json:" in content
    assert '"k": 1' in content
    assert "immovables[0]{title,start,end}:" in content
    assert "durable_constraints[1]{name,necessity,scope,status,source,description}:" in content


@pytest.mark.asyncio
async def test_run_stage_gate_returns_safe_fallback_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed model output should not crash stage execution."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)

    async def _noop_ensure_stage_agents(self: TimeboxingFlowAgent) -> None:
        return None

    monkeypatch.setattr(TimeboxingFlowAgent, "_ensure_stage_agents", _noop_ensure_stage_agents)
    agent._stage_agents = {  # type: ignore[attr-defined]
        TimeboxingStage.COLLECT_CONSTRAINTS: _MalformedStageAgent()
    }

    context = {"facts": {"timezone": "Europe/Amsterdam"}}
    out = await TimeboxingFlowAgent._run_stage_gate(  # type: ignore[misc]
        agent,
        stage=TimeboxingStage.COLLECT_CONSTRAINTS,
        user_message="use defaults",
        context=context,
    )

    assert out.stage_id == TimeboxingStage.COLLECT_CONSTRAINTS
    assert out.ready is False
    assert "stage retry required" in out.missing
    assert "_stage_gate_error" in out.facts
    assert out.facts["timezone"] == "Europe/Amsterdam"
