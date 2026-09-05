"""An `Asked` outcome is answered by planner_agent with the session described,
in the turn's own reply. No stage card is drawn, no session state moves, and
an answerer that fails is reported, never swallowed and never retried into a
session start.

Fixture shape copied from `test_turn_cancels_ladder.py`: Kernel/Repo/Runtime
fakes plus the four monkeypatches that let `_run_adaptive_timebox_turn` run
end to end without Slack, a planner or a store.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from autogen_agentchat.messages import TextMessage

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    Asked,
    AskQuestion,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_cards import timebox_failure_message


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


async def _question_intent(*a, **k):
    return AskQuestion(question="Is it planned?")


def _fixture(monkeypatch, *, reply=None, raise_=None):
    sent: list[tuple[object, object]] = []

    class Kernel:
        async def turn(self, request, progress):
            assert isinstance(request.intent, AskQuestion)
            return Asked(question=request.intent.question)

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return PlanningSessionSnapshot(
                session_key=key, revision=4, owner_user_id=owner_user_id
            )

    class Runtime:
        timeboxing_session_store = Repo()

        async def send_message(self, message, recipient):
            sent.append((message, recipient))
            if raise_ is not None:
                raise raise_
            return SimpleNamespace(
                chat_message=TextMessage(content=reply, source="planner_agent")
            )

    def _no_card(*a, **k):
        raise AssertionError("present_outcome must not run for Asked")

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", _question_intent)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    monkeypatch.setattr(handlers, "present_outcome", _no_card)
    return Runtime(), sent


async def _turn(runtime):
    return await handlers._run_adaptive_timebox_turn(
        runtime=runtime, client=object(), logger=logging.getLogger(__name__),
        session_key="D1:dm", actor_user_id="U1", interaction_id="1.1",
        progress_channel="D1", progress_ts="1.0",
        card_channel="D1", card_thread_ts="dm", user_text="Is it planned?",
    )


@pytest.mark.asyncio
async def test_a_question_is_answered_by_planner_agent_with_the_session_described(monkeypatch):
    runtime, sent = _fixture(monkeypatch, reply="No — nothing on the calendar today.")
    message = await _turn(runtime)
    assert len(sent) == 1
    msg, recipient = sent[0]
    assert recipient.type == "planner_agent"
    assert msg.source == "U1"
    assert "Is it planned?" in msg.content
    assert "timeboxing session" in msg.content       # the description came along
    assert message.text == "No — nothing on the calendar today."
    assert message.blocks == [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "No — nothing on the calendar today."},
        }
    ]


@pytest.mark.asyncio
async def test_an_answerer_that_fails_is_reported_and_never_starts_a_session(monkeypatch):
    errors: list[dict] = []
    monkeypatch.setattr(handlers, "record_error", lambda **kw: errors.append(kw))
    runtime, sent = _fixture(monkeypatch, raise_=RuntimeError("planner down"))
    message = await _turn(runtime)
    assert len(sent) == 1                              # asked once, not retried
    assert errors == [{"component": "surface_intent", "error_type": "answer_failure"}]
    assert message.text == timebox_failure_message(snapshot=None).text
