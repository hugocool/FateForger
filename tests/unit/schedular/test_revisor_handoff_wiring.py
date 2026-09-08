from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat.agents")

from autogen_agentchat.messages import HandoffMessage, TextMessage
from autogen_core import CancellationToken, DefaultTopicId, MessageContext

from fateforger.agents.revisor import agent as revisor_agent_module
from fateforger.agents.revisor.messages import (
    ReviewIntentDecision,
    WeeklyReviewPhase,
    WeeklyReviewRecap,
    WeeklyReviewTurn,
)


def _ctx() -> MessageContext:
    return MessageContext(
        sender=None,
        topic_id=DefaultTopicId(),
        is_rpc=False,
        cancellation_token=CancellationToken(),
        message_id="m1",
    )


def test_revisor_passes_allowed_handoffs_to_general_assistant(monkeypatch):
    captured_instances: list[dict[str, object]] = []

    class DummyAssistantAgent:
        def __init__(self, **kwargs):
            captured_instances.append(kwargs)

    monkeypatch.setattr(revisor_agent_module, "AssistantAgent", DummyAssistantAgent)
    monkeypatch.setattr(
        revisor_agent_module, "build_autogen_chat_client", lambda _name: object()
    )

    handoffs = ["tasks_agent_handoff"]
    revisor_agent_module.RevisorAgent("revisor_agent", allowed_handoffs=handoffs)

    assert len(captured_instances) == 3
    assert captured_instances[0].get("handoffs") == handoffs
    assert captured_instances[1].get("output_content_type") == ReviewIntentDecision
    assert captured_instances[2].get("output_content_type") == WeeklyReviewTurn


@pytest.mark.asyncio
async def test_revisor_handle_text_returns_handoff_message_when_not_in_review_session():
    class _FakeIntentAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            return SimpleNamespace(chat_message=SimpleNamespace(content=ReviewIntentDecision()))

    class _FakeGeneralAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            return SimpleNamespace(
                chat_message=HandoffMessage(
                    target="tasks_agent",
                    content="handoff",
                    source="revisor_agent",
                )
            )

    agent = revisor_agent_module.RevisorAgent("revisor_agent", allowed_handoffs=[])
    agent._intent_assistant = _FakeIntentAssistant()
    agent._assistant = _FakeGeneralAssistant()

    result = await agent.handle_text(TextMessage(content="refine sprint", source="U1"), _ctx())

    assert isinstance(result, HandoffMessage)
    assert result.target == "tasks_agent"


@pytest.mark.asyncio
async def test_revisor_guided_session_progresses_when_gates_are_met():
    turns = [
        WeeklyReviewTurn(
            phase=WeeklyReviewPhase.REFLECT,
            gate_met=True,
            assistant_message="Captured reflect.",
            phase_summary=["wins+misses+progress"],
        ),
        WeeklyReviewTurn(
            phase=WeeklyReviewPhase.SCAN_BOARD,
            gate_met=True,
            assistant_message="Captured scan.",
            phase_summary=["3 active items reviewed"],
        ),
        WeeklyReviewTurn(
            phase=WeeklyReviewPhase.OUTCOMES,
            gate_met=True,
            assistant_message="Captured outcomes.",
            phase_summary=["must outcome defined"],
        ),
        WeeklyReviewTurn(
            phase=WeeklyReviewPhase.SYSTEMS_RISKS,
            gate_met=True,
            assistant_message="Captured systems + risks.",
            phase_summary=["SSC + one risk mitigation"],
        ),
        WeeklyReviewTurn(
            phase=WeeklyReviewPhase.CLOSE,
            gate_met=True,
            assistant_message="Closing recap.",
            session_complete=True,
            recap=WeeklyReviewRecap(
                summary="Week plan aligned.",
                weekly_intention="Protect deep work blocks.",
                weekly_constraints=["No meetings before 12:00"],
            ),
        ),
    ]

    class _FakeIntentAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            decision = ReviewIntentDecision(start_session=True, rationale="weekly review request")
            return SimpleNamespace(chat_message=SimpleNamespace(content=decision))

    class _FakeGuidedAssistant:
        def __init__(self, scripted_turns: list[WeeklyReviewTurn]):
            self._turns = list(scripted_turns)

        async def on_messages(self, _messages, _cancellation_token):
            return SimpleNamespace(chat_message=SimpleNamespace(content=self._turns.pop(0)))

    class _FakeGeneralAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            return SimpleNamespace(chat_message=TextMessage(content="general", source="revisor_agent"))

    agent = revisor_agent_module.RevisorAgent("revisor_agent", allowed_handoffs=[])
    agent._intent_assistant = _FakeIntentAssistant()
    agent._guided_assistant = _FakeGuidedAssistant(turns)
    agent._assistant = _FakeGeneralAssistant()

    first = await agent.handle_text(
        TextMessage(content="Can we do a weekly review now?", source="U1"),
        _ctx(),
    )
    assert isinstance(first, TextMessage)
    assert "Phase 1/5 (Reflect)" in first.content
    assert "Gate: ⏳ pending" in first.content

    for idx in range(5):
        resp = await agent.handle_text(
            TextMessage(content=f"turn-{idx}", source="U1"),
            _ctx(),
        )
        assert isinstance(resp, TextMessage)

    assert agent._session is None
    assert "U1" in agent._latest_recap_by_user
    recap = agent._latest_recap_by_user["U1"]
    assert recap.summary == "Week plan aligned."
    assert recap.weekly_constraints == ["No meetings before 12:00"]


@pytest.mark.asyncio
async def test_revisor_guided_session_blocks_when_gate_not_met():
    class _FakeIntentAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            decision = ReviewIntentDecision(start_session=True, rationale="weekly review request")
            return SimpleNamespace(chat_message=SimpleNamespace(content=decision))

    class _FakeGuidedAssistant:
        async def on_messages(self, _messages, _cancellation_token):
            turn = WeeklyReviewTurn(
                phase=WeeklyReviewPhase.REFLECT,
                gate_met=False,
                assistant_message="Need one miss and progress update.",
                missing_fields=["1 miss", "progress vs goals"],
            )
            return SimpleNamespace(chat_message=SimpleNamespace(content=turn))

    agent = revisor_agent_module.RevisorAgent("revisor_agent", allowed_handoffs=[])
    agent._intent_assistant = _FakeIntentAssistant()
    agent._guided_assistant = _FakeGuidedAssistant()

    await agent.handle_text(
        TextMessage(content="Start weekly review", source="U1"),
        _ctx(),
    )
    blocked = await agent.handle_text(
        TextMessage(content="one win only", source="U1"),
        _ctx(),
    )

    assert isinstance(blocked, TextMessage)
    assert "Gate: ❌ not met" in blocked.content
    assert "Still needed" in blocked.content
    assert agent._session is not None
    assert agent._session.phase == WeeklyReviewPhase.REFLECT
