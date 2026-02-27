from __future__ import annotations

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.contracts import TaskCandidate


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
