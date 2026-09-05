"""An `Asked` outcome is answered by planner_agent with the session described,
in the turn's own reply. No stage card is drawn, no session state moves, and
an answerer that fails is reported, never swallowed and never retried into a
session start.

Asked is not started on the host either: a question marks no activity, cancels
no Admonisher ladder, and persists no session row. The Admonisher reads the
session store to decide whether to nudge, so a row written by a question is a
planning reminder silenced for an hour by the user asking whether they have
planned anything.

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
    Advance,
    ArtifactKind,
    Asked,
    AskQuestion,
    PlanningArtifact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_cards import timebox_failure_message


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


class _Repo:
    """Loads without creating; `load_or_create` is the only thing that writes."""

    def __init__(self, snapshot: PlanningSessionSnapshot | None = None) -> None:
        self.rows: dict[str, PlanningSessionSnapshot] = (
            {} if snapshot is None else {snapshot.session_key: snapshot}
        )

    async def load(self, key):
        return self.rows.get(key)

    async def load_or_create(self, key, owner_user_id):
        row = self.rows.get(key)
        if row is None:
            row = PlanningSessionSnapshot.new(
                session_key=key, owner_user_id=owner_user_id
            )
            self.rows[key] = row
        return row


class _Haunting:
    def __init__(self) -> None:
        self.activity: list[str] = []
        self.cancelled: list[str] = []

    async def record_user_activity(self, *, topic_id, task_id, user_id):
        self.activity.append(topic_id)

    async def cancel_followups(self, *, topic_id):
        self.cancelled.append(topic_id)


class _ActivityRecorder:
    def __init__(self) -> None:
        self.active: list[str] = []
        self.inactive: list[str] = []

    def mark_active(self, *, user_id, channel_id, thread_ts):
        self.active.append(user_id)

    def mark_inactive(self, *, user_id):
        self.inactive.append(user_id)


def _committed_snapshot(session_key: str = "D1:dm") -> PlanningSessionSnapshot:
    receipt = PlanningArtifact.create(
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"committed": True, "tx_id": "tx_7", "candidate_digest": "d" * 64},
        dependency_revisions={},
    )
    return PlanningSessionSnapshot(
        session_key=session_key,
        revision=8,
        owner_user_id="U1",
        status="committed",
        artifacts=[receipt],
    )


def _fixture(
    monkeypatch,
    *,
    reply=None,
    raise_=None,
    existing: PlanningSessionSnapshot | None = None,
    intent=None,
    kernel_raises: Exception | None = None,
):
    sent: list[tuple[object, object]] = []
    turn_intent = AskQuestion(question="Is it planned?") if intent is None else intent

    async def _intent(*a, **k):
        return turn_intent

    class Kernel:
        async def turn(self, request, progress):
            if kernel_raises is not None:
                raise kernel_raises
            assert isinstance(request.intent, AskQuestion)
            return Asked(question=request.intent.question)

    class Runtime:
        timeboxing_session_store = _Repo(existing)
        haunting_service = _Haunting()

        async def send_message(self, message, recipient):
            sent.append((message, recipient))
            if raise_ is not None:
                raise raise_
            return SimpleNamespace(
                chat_message=TextMessage(content=reply, source="planner_agent")
            )

    def _no_card(*a, **k):
        raise AssertionError("present_outcome must not run for Asked")

    activity = _ActivityRecorder()
    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", _intent)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    monkeypatch.setattr(handlers, "present_outcome", _no_card)
    monkeypatch.setattr(handlers, "timeboxing_activity", activity)
    runtime = Runtime()
    runtime.activity = activity
    return runtime, sent


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
async def test_a_question_leaves_no_session_row_and_no_activity(monkeypatch):
    """The Admonisher decides by the store and the idle timer; a question must
    move neither, or asking "is it planned?" silences the reminder that would
    have answered it."""

    runtime, _ = _fixture(monkeypatch, reply="Nothing on the calendar today.")
    await _turn(runtime)

    assert runtime.activity.active == []
    assert runtime.haunting_service.activity == []
    assert runtime.timeboxing_session_store.rows == {}


@pytest.mark.asyncio
async def test_a_turn_that_is_not_a_question_still_records_activity(monkeypatch):
    """The other side of the same seam: only a question is exempt."""

    runtime, _ = _fixture(
        monkeypatch, intent=Advance(), kernel_raises=RuntimeError("kernel down")
    )
    await _turn(runtime)

    assert runtime.haunting_service.activity == ["D1:dm"]
    assert runtime.activity.active == ["U1"]


@pytest.mark.asyncio
async def test_an_answerer_that_fails_is_reported_and_never_starts_a_session(monkeypatch):
    errors: list[dict] = []
    monkeypatch.setattr(handlers, "record_error", lambda **kw: errors.append(kw))
    runtime, sent = _fixture(monkeypatch, raise_=RuntimeError("planner down"))
    message = await _turn(runtime)
    assert len(sent) == 1                              # asked once, not retried
    assert errors == [{"component": "surface_intent", "error_type": "answer_failure"}]
    # The snapshot the answer was described from is the one the failure is
    # reported against; on a session with no row that is a fresh one.
    assert message.text == timebox_failure_message(
        snapshot=PlanningSessionSnapshot.new(session_key="D1:dm", owner_user_id="U1")
    ).text
    assert runtime.timeboxing_session_store.rows == {}


@pytest.mark.asyncio
async def test_an_answerer_that_fails_on_a_committed_day_says_the_day_still_stands(monkeypatch):
    """The two failure sentences differ, and the code picks by the receipt. A
    test that passed `snapshot=None` could not tell the branch apart from one
    that passed nothing at all."""

    monkeypatch.setattr(handlers, "record_error", lambda **kw: None)
    runtime, sent = _fixture(
        monkeypatch, raise_=RuntimeError("planner down"), existing=_committed_snapshot()
    )
    message = await _turn(runtime)
    assert len(sent) == 1
    assert message.text == timebox_failure_message(snapshot=_committed_snapshot()).text
    assert message.text != timebox_failure_message(snapshot=None).text
