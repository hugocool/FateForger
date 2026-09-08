"""Stage 1 answers and suspensions are offered to memory after the save, as
reader feedback with provenance: recorded, never acted on."""
from __future__ import annotations

import logging

from fateforger.agents.timeboxing.feedback import (
    RecordingFeedbackObserver,
    feedback_facts,
)
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.haunt.timeboxing_activity import timeboxing_activity


def _snapshot(*facts) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(session_key="C1:1.0", revision=1, owner_user_id="U1", facts=list(facts))


def test_only_new_user_sourced_stage_one_facts_are_feedback() -> None:
    old = PlanningFact(fact_id="elicited:x:1", kind=FactKind.ELICITED_STATEMENT, value={"cell": None, "text": "old"}, source="user")
    new = PlanningFact(fact_id="elicited:x:2", kind=FactKind.ELICITED_STATEMENT, value={"cell": None, "text": "new"}, source="user")
    suspend = PlanningFact(fact_id="suspend:c1", kind=FactKind.SUSPENDED_CONSTRAINT, value={"uid": "c1", "reason": "not today"}, source="user")
    request = PlanningFact(fact_id="activity-1", kind=FactKind.REQUESTED_ACTIVITY, value="gym", source="user")
    system = PlanningFact(fact_id="coverage:2026-09-08", kind=FactKind.COVERAGE_MATRIX, value={"cells": {}}, source="system")

    picked = feedback_facts(_snapshot(old), _snapshot(old, new, suspend, request, system))

    assert [f.fact_id for f in picked] == ["elicited:x:2", "suspend:c1"]


async def test_the_recording_observer_keeps_what_it_was_given() -> None:
    observer = RecordingFeedbackObserver()
    fact = PlanningFact(fact_id="suspend:c1", kind=FactKind.SUSPENDED_CONSTRAINT, value={"uid": "c1", "reason": "not today"}, source="user")
    await observer.observe(session_key="C1:1.0", facts=[fact])
    assert observer.observed == [("C1:1.0", [fact])]


class _Client:
    """The pieces `HarnessProgressCard` needs; posting failures are swallowed
    by the progress channel itself, so a fake this small is enough."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.updates: list[dict] = []

    async def chat_postMessage(self, **payload):
        self.posts.append(payload)
        return {"ok": True, "channel": payload.get("channel", "C1"), "ts": f"progress-{len(self.posts)}"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}


class _SessionRuntime:
    """A runtime carrying only what the adaptive kernel route may read.

    Copied from `tests/unit/test_harness_approval_action.py::_SessionRuntime`,
    with the constraint store and calendar id dropped: the turn this test
    drives never locks a planning day, so `HostPlanningContext` never reaches
    either.
    """

    def __init__(self, *, repository, planner, feedback_observer=None) -> None:
        self.timeboxing_session_store = repository
        self.timeboxing_planner = planner
        if feedback_observer is not None:
            self.timeboxing_feedback_observer = feedback_observer

    async def send_message(self, message, recipient):
        raise AssertionError("the kernel route must not reach the AutoGen runtime")


async def test_the_turn_offers_the_new_suspended_constraint_to_the_observer() -> None:
    """`ProvidePlanningFacts` carrying a suspension is the shape a real Stage 1
    "not today" press takes; the observer must see exactly that fact, keyed to
    the session, after the save the turn just made."""

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts
    from fateforger.slack_bot import handlers
    from fateforger.slack_bot.timeboxing_intents import TimeboxActionEnvelope

    session_key = "C1:1.0"
    fact = PlanningFact(
        fact_id="suspend:c1",
        kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c1", "reason": "not today"},
        source="user",
    )
    observer = RecordingFeedbackObserver()
    runtime = _SessionRuntime(
        repository=InMemoryPlanningSessionRepository([]),
        planner=object(),
        feedback_observer=observer,
    )

    await handlers._run_adaptive_timebox_turn(
        runtime=runtime,
        client=_Client(),
        logger=logging.getLogger("test.feedback"),
        session_key=session_key,
        actor_user_id="U1",
        interaction_id="1.1",
        progress_channel="C1",
        progress_ts="1.0",
        card_channel="C1",
        card_thread_ts="1.0",
        action=TimeboxActionEnvelope(
            session_key=session_key,
            expected_revision=0,
            intent=ProvidePlanningFacts(facts=[fact]),
        ),
    )

    timeboxing_activity.mark_inactive(user_id="U1")
    assert observer.observed == [(session_key, [fact])]


async def test_a_failing_observer_does_not_fail_the_turn(caplog) -> None:
    """The invariant this task guarantees: feedback is best-effort. A memory
    outage must not turn a saved planning turn into a failed Slack reply."""

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts
    from fateforger.slack_bot import handlers
    from fateforger.slack_bot.timeboxing_intents import TimeboxActionEnvelope

    class _RaisingObserver:
        async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None:
            raise RuntimeError("memory is unreachable")

    session_key = "C1:2.0"
    fact = PlanningFact(
        fact_id="suspend:c1",
        kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c1", "reason": "not today"},
        source="user",
    )
    runtime = _SessionRuntime(
        repository=InMemoryPlanningSessionRepository([]),
        planner=object(),
        feedback_observer=_RaisingObserver(),
    )

    with caplog.at_level(logging.WARNING, logger="test.feedback"):
        outcome = await handlers._run_adaptive_timebox_turn(
            runtime=runtime,
            client=_Client(),
            logger=logging.getLogger("test.feedback"),
            session_key=session_key,
            actor_user_id="U1",
            interaction_id="2.1",
            progress_channel="C1",
            progress_ts="2.0",
            card_channel="C1",
            card_thread_ts="2.0",
            action=TimeboxActionEnvelope(
                session_key=session_key,
                expected_revision=0,
                intent=ProvidePlanningFacts(facts=[fact]),
            ),
        )

    timeboxing_activity.mark_inactive(user_id="U1")
    assert outcome is not None
    assert "stage1 feedback not recorded" in caplog.text
