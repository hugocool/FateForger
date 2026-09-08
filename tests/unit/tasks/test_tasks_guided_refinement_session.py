from __future__ import annotations

import types

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat.agents")

from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken, DefaultTopicId, MessageContext

from fateforger.agents.tasks import agent as tasks_agent_module
from fateforger.agents.tasks.messages import (
    GuidedRefinementPhase,
    GuidedRefinementRecap,
    GuidedRefinementRecapRequest,
    GuidedRefinementTurn,
)


def _ctx() -> MessageContext:
    return MessageContext(
        sender=None,
        topic_id=DefaultTopicId(),
        is_rpc=False,
        cancellation_token=CancellationToken(),
        message_id="m1",
    )


def _build_agent(monkeypatch: pytest.MonkeyPatch) -> tasks_agent_module.TasksAgent:
    class DummyAssistantAgent:
        def __init__(self, **_kwargs):
            return None

    monkeypatch.setattr(tasks_agent_module, "AssistantAgent", DummyAssistantAgent)
    monkeypatch.setattr(tasks_agent_module, "build_autogen_chat_client", lambda _name: object())
    return tasks_agent_module.TasksAgent("tasks_agent")


@pytest.mark.asyncio
async def test_start_guided_refinement_session_sets_phase_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)

    result = await agent.handle_text(
        TextMessage(content="start guided task refinement session", source="U1"),
        _ctx(),
    )

    assert "Guided Refinement — Phase 1/4 (Scope)" in result.content
    assert "Gate: ⏳ pending" in result.content
    assert agent._guided_session is not None
    assert agent._guided_session.phase == GuidedRefinementPhase.SCOPE


@pytest.mark.asyncio
async def test_guided_session_gate_not_met_stays_in_same_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)
    await agent.handle_text(
        TextMessage(content="start guided task refinement session", source="U1"),
        _ctx(),
    )

    class _FakeGuidedAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            return types.SimpleNamespace(
                chat_message=types.SimpleNamespace(
                    content=GuidedRefinementTurn(
                        phase=GuidedRefinementPhase.SCOPE,
                        gate_met=False,
                        missing_fields=["project areas"],
                        phase_summary=["Work board selected"],
                        assistant_message="Need project areas and personal scope.",
                    )
                )
            )

    async def _timeout_passthrough(_label, awaitable, *, timeout_s):
        _ = timeout_s
        return await awaitable

    monkeypatch.setattr(tasks_agent_module, "with_timeout", _timeout_passthrough)
    agent._guided_assistant = _FakeGuidedAssistant()

    result = await agent.handle_text(
        TextMessage(content="Work board only", source="U1"), _ctx()
    )

    assert "Gate: ❌ not met" in result.content
    assert "Still needed" in result.content
    assert agent._guided_session is not None
    assert agent._guided_session.phase == GuidedRefinementPhase.SCOPE


@pytest.mark.asyncio
async def test_guided_session_gate_met_advances_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)
    await agent.handle_text(
        TextMessage(content="start guided task refinement session", source="U1"),
        _ctx(),
    )

    class _FakeGuidedAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            return types.SimpleNamespace(
                chat_message=types.SimpleNamespace(
                    content=GuidedRefinementTurn(
                        phase=GuidedRefinementPhase.SCOPE,
                        gate_met=True,
                        phase_summary=["Work + personal scope selected"],
                        assistant_message="Scope captured. Proceeding.",
                    )
                )
            )

    async def _timeout_passthrough(_label, awaitable, *, timeout_s):
        _ = timeout_s
        return await awaitable

    monkeypatch.setattr(tasks_agent_module, "with_timeout", _timeout_passthrough)
    agent._guided_assistant = _FakeGuidedAssistant()

    await agent.handle_text(TextMessage(content="scope details", source="U1"), _ctx())

    assert agent._guided_session is not None
    assert agent._guided_session.phase == GuidedRefinementPhase.SCAN


@pytest.mark.asyncio
async def test_guided_session_close_persists_recap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)
    agent._guided_session = tasks_agent_module.GuidedRefinementSessionState(
        phase=GuidedRefinementPhase.CLOSE,
        user_id="U1",
    )

    recap = GuidedRefinementRecap(
        summary="Refined four items across work + personal.",
        user_intention="Execute top two next actions tomorrow.",
        stuck_or_postponed_signals=["Ticket A postponed 3 times"],
    )

    class _FakeGuidedAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            return types.SimpleNamespace(
                chat_message=types.SimpleNamespace(
                    content=GuidedRefinementTurn(
                        phase=GuidedRefinementPhase.CLOSE,
                        gate_met=True,
                        assistant_message="Session complete.",
                        recap=recap,
                        session_complete=True,
                    )
                )
            )

    async def _timeout_passthrough(_label, awaitable, *, timeout_s):
        _ = timeout_s
        return await awaitable

    monkeypatch.setattr(tasks_agent_module, "with_timeout", _timeout_passthrough)
    agent._guided_assistant = _FakeGuidedAssistant()

    result = await agent.handle_text(TextMessage(content="close", source="U1"), _ctx())
    assert "Session recap" in result.content
    assert agent._guided_session is None

    recap_response = await agent.handle_guided_recap_request(
        GuidedRefinementRecapRequest(user_id="U1"),
        _ctx(),
    )
    assert recap_response.found is True
    assert recap_response.recap is not None
    assert recap_response.recap.summary == recap.summary
