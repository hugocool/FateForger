from __future__ import annotations

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.contracts import TaskCandidate
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
)


def test_capture_inputs_context_injects_prefetched_tasks_when_input_missing() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.prefetched_pending_tasks = [
        TaskCandidate(title="Write weekly report", block_count=1),
        TaskCandidate(title="Reply to investor email"),
    ]
    session.input_facts = {"daily_one_thing": {"title": "Ship patch"}}

    context = agent._build_capture_inputs_context(session, user_message="")  # noqa: SLF001
    tasks = context["input_facts"]["tasks"]

    assert len(tasks) == 2
    assert tasks[0]["title"] == "Write weekly report"
    assert tasks[1]["title"] == "Reply to investor email"


def test_capture_inputs_context_keeps_existing_tasks_over_prefetch() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.prefetched_pending_tasks = [TaskCandidate(title="Should not replace")]
    session.input_facts = {"tasks": [{"title": "User supplied task", "block_count": 2}]}

    context = agent._build_capture_inputs_context(session, user_message="")  # noqa: SLF001
    tasks = context["input_facts"]["tasks"]

    assert len(tasks) == 1
    assert tasks[0]["title"] == "User supplied task"


def _session_scope_constraint(*, aspect_id: str, name: str = "Scope signal") -> Constraint:
    return Constraint(
        user_id="u1",
        channel_id="c1",
        thread_ts="t1",
        name=name,
        description=name,
        necessity=ConstraintNecessity.MUST,
        scope=ConstraintScope.SESSION,
        hints={"aspect_classification": {"aspect_id": aspect_id}},
    )


def test_capture_inputs_context_suppresses_prefetch_when_gtd_admin_exclusion_active() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.prefetched_pending_tasks = [
        TaskCandidate(title="Factuur van Coolblue"),
        TaskCandidate(title="Review my Next Actions list"),
    ]
    session.active_constraints = [
        _session_scope_constraint(
            aspect_id="gtd_admin_exclusion", name="Exclude GTD/Admin"
        )
    ]

    context = agent._build_capture_inputs_context(session, user_message="")  # noqa: SLF001

    assert context["input_facts"].get("tasks") in (None, [])


def test_capture_inputs_context_suppresses_prefetch_when_daily_one_thing_active() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(thread_ts="t1", channel_id="c1", user_id="u1")
    session.prefetched_pending_tasks = [
        TaskCandidate(title="Generic admin task"),
        TaskCandidate(title="Inbox cleanup"),
    ]
    session.active_constraints = [
        _session_scope_constraint(aspect_id="daily_one_thing", name="Daily One Thing")
    ]

    context = agent._build_capture_inputs_context(session, user_message="")  # noqa: SLF001

    assert context["input_facts"].get("tasks") in (None, [])
