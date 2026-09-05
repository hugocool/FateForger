"""Every turn on a planning session is activity on that session's Admonisher
follow-up ladder: it cancels a pending nudge (`record_user_activity`), and a
turn that leaves the session `committed` or `cancelled` cancels any remaining
follow-ups outright (`cancel_followups`). Both are best-effort against
`runtime.haunting_service`, which most unit fakes do not set.

Fixture shape copied from `test_adaptive_turn_marks_timeboxing_active.py`,
which already drives `_run_adaptive_timebox_turn` end to end with minimal
fakes -- kept in its own file because that fixture (Kernel/Repo/Runtime plus
four monkeypatches) is sizeable enough that folding it into
`test_slack_timeboxing_routing.py`'s already-heavy fixture set would bury it.
"""

from __future__ import annotations

import logging

import fateforger.slack_bot.handlers as handlers
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    PlanningSessionSnapshot,
    TurnFailed,
)


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


async def _noop_intent(*a, **k):
    return Advance()


class _HauntingService:
    def __init__(self):
        self.activity: list[dict] = []
        self.cancelled: list[dict] = []

    async def record_user_activity(self, **kwargs):
        self.activity.append(kwargs)
        return 0

    async def cancel_followups(self, **kwargs):
        self.cancelled.append(kwargs)
        return 0


async def test_a_turn_records_activity_on_the_session_topic(monkeypatch) -> None:
    haunting = _HauntingService()

    class Kernel:
        async def turn(self, request, progress):
            return TurnFailed(code="x", message="x")

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return PlanningSessionSnapshot(
                session_key=key, revision=1, owner_user_id=owner_user_id
            )

    class Runtime:
        timeboxing_session_store = Repo()
        haunting_service = haunting

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", _noop_intent)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    monkeypatch.setattr(handlers, "present_outcome", lambda *a, **k: ("rendered", None))

    await handlers._run_adaptive_timebox_turn(
        runtime=Runtime(), client=object(), logger=logging.getLogger(__name__),
        session_key="C1:1.0", actor_user_id="U1", interaction_id="1.1",
        progress_channel="C1", progress_ts="1.0",
        card_channel="C1", card_thread_ts="1.0", user_text="hello",
    )

    assert haunting.activity == [
        {"topic_id": "C1:1.0", "task_id": None, "user_id": "U1"}
    ]
    assert haunting.cancelled == []


async def test_a_turn_that_ends_the_session_cancels_the_ladder(monkeypatch) -> None:
    haunting = _HauntingService()

    class Kernel:
        async def turn(self, request, progress):
            return TurnFailed(code="x", message="x")

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return PlanningSessionSnapshot(
                session_key=key, revision=1, owner_user_id=owner_user_id,
                status="committed",
            )

    class Runtime:
        timeboxing_session_store = Repo()
        haunting_service = haunting

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", _noop_intent)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)
    monkeypatch.setattr(handlers, "present_outcome", lambda *a, **k: ("rendered", None))

    await handlers._run_adaptive_timebox_turn(
        runtime=Runtime(), client=object(), logger=logging.getLogger(__name__),
        session_key="C1:2.0", actor_user_id="U2", interaction_id="2.1",
        progress_channel="C1", progress_ts="2.0",
        card_channel="C1", card_thread_ts="2.0", user_text="yes commit it",
    )

    assert haunting.cancelled == [{"topic_id": "C1:2.0"}]
    assert haunting.activity == [
        {"topic_id": "C1:2.0", "task_id": None, "user_id": "U2"}
    ]
