from __future__ import annotations

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage

from fateforger.agents.tasks.messages import PendingTaskItem, PendingTaskSnapshot
from fateforger.agents.timeboxing.contracts import TaskCandidate
from fateforger.agents.timeboxing.task_marshalling_capability import (
    TaskAssistRequest,
    TaskMarshallingCapability,
)


class _Session:
    def __init__(self) -> None:
        self.user_id = "u1"
        self.channel_id = "c1"
        self.thread_ts = "t1"
        self.session_key = "k1"
        self.input_facts: dict = {}
        self.prefetched_pending_tasks: list[TaskCandidate] = []
        self.pending_tasks_prefetch = False


def _cap(send):
    return TaskMarshallingCapability(
        send_message=send,
        timeout_s=3.0,
        source_resolver=lambda: "timeboxing_agent",
    )


def test_assist_request_text_message_is_typed_and_deterministic() -> None:
    assert (
        TaskAssistRequest(user_message="show pending tasks", note=None).to_text_message()
        == "show pending tasks"
    )
    assert (
        TaskAssistRequest(
            user_message="show pending tasks",
            note="request came from assist flow",
        ).to_text_message()
        == "show pending tasks\n\nAssist context: request came from assist flow"
    )


def test_merge_prefetched_tasks_respects_existing_user_tasks() -> None:
    merged = TaskMarshallingCapability.merge_prefetched_tasks(
        input_facts={"tasks": [{"title": "User task"}]},
        prefetched=[TaskCandidate(title="Prefetched task")],
    )
    assert merged["tasks"][0]["title"] == "User task"


@pytest.mark.asyncio
async def test_request_pending_tasks_uses_snapshot_and_returns_candidates() -> None:
    async def _send(message, recipient, cancellation_token):  # noqa: ARG001
        assert recipient.type == "tasks_agent"
        return PendingTaskSnapshot(
            items=[PendingTaskItem(id="1", title="Write PR notes")],
            summary="Found 1 pending task(s).",
        )

    session = _Session()
    tasks = await _cap(_send).request_pending_tasks(
        session=session,
        query="pending",
        limit=10,
    )
    assert [task.title for task in tasks] == ["Write PR notes"]


@pytest.mark.asyncio
async def test_assist_tasks_forwards_generic_task_query() -> None:
    async def _send(message, recipient, cancellation_token):  # noqa: ARG001
        assert recipient.type == "tasks_agent"
        assert isinstance(message, TextMessage)
        assert message.content == "help me triage tasks\n\nAssist context: adjacent question"
        return TextMessage(content="Task Marshal response", source="tasks_agent")

    session = _Session()
    out = await _cap(_send).assist_response(
        session=session,
        user_message="help me triage tasks",
        note="adjacent question",
    )
    assert out == "Task Marshal response"


@pytest.mark.asyncio
async def test_assist_returns_none_for_empty_user_message() -> None:
    async def _send(message, recipient, cancellation_token):  # noqa: ARG001
        raise AssertionError("send_message should not be called")

    session = _Session()
    out = await _cap(_send).assist_response(
        session=session,
        user_message="   ",
        note="assist request",
    )
    assert out is None


@pytest.mark.asyncio
async def test_assist_routes_notion_sprint_query_to_tasks_agent() -> None:
    async def _send(message, recipient, cancellation_token):  # noqa: ARG001
        assert recipient.type == "tasks_agent"
        assert isinstance(message, TextMessage)
        assert message.content == (
            "show pending sprint tickets in notion\n\nAssist context: assist request"
        )
        return TextMessage(content="Sprint query handled", source="tasks_agent")

    session = _Session()
    out = await _cap(_send).assist_response(
        session=session,
        user_message="show pending sprint tickets in notion",
        note="assist request",
    )
    assert out == "Sprint query handled"
