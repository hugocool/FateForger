from __future__ import annotations

import types

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat.agents")

from fateforger.agents.tasks import agent as tasks_agent_module
from fateforger.agents.tasks.messages import PendingTaskSnapshotRequest


@pytest.mark.asyncio
async def test_tasks_agent_pending_snapshot_returns_typed_rows(monkeypatch):
    class DummyAssistantAgent:
        def __init__(self, **_kwargs):
            return None

    monkeypatch.setattr(tasks_agent_module, "AssistantAgent", DummyAssistantAgent)
    monkeypatch.setattr(tasks_agent_module, "build_autogen_chat_client", lambda _name: object())

    agent = tasks_agent_module.TasksAgent("tasks_agent")

    async def _fake_list_pending_tasks(*, limit: int, per_project_limit: int):
        assert limit == 5
        assert per_project_limit == 2
        return [
            types.SimpleNamespace(
                id="t1",
                title="Prepare roadmap update",
                project_id="p1",
                project_name="Work",
            ),
            types.SimpleNamespace(
                id="t2",
                title="Book dentist",
                project_id="p2",
                project_name="Personal",
            ),
        ]

    agent._list_manager.list_pending_tasks = _fake_list_pending_tasks  # type: ignore[method-assign]

    response = await agent.handle_pending_snapshot(
        PendingTaskSnapshotRequest(
            user_id="u1",
            limit=5,
            per_project_limit=2,
        ),
        ctx=types.SimpleNamespace(),
    )

    assert response.summary == "Found 2 pending task(s)."
    assert [item.title for item in response.items] == [
        "Prepare roadmap update",
        "Book dentist",
    ]
