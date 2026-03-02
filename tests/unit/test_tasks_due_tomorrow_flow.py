from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat.agents")

from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken, DefaultTopicId, MessageContext

from fateforger.agents.tasks import agent as tasks_agent_module
from fateforger.agents.tasks.defaults_memory import TaskDueDefaults
from fateforger.slack_bot.messages import SlackBlockMessage


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
async def test_due_tomorrow_asks_for_defaults_once(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _build_agent(monkeypatch)

    async def _no_defaults(*, user_id: str):
        _ = user_id
        return None

    monkeypatch.setattr(agent._defaults_store, "get_user_defaults", _no_defaults)

    result = await agent.handle_text(
        TextMessage(content="Which tasks are due tomorrow?", source="U1"),
        _ctx(),
    )

    assert isinstance(result, TextMessage)
    assert "remember this" in result.content.lower()
    assert "U1" in agent._pending_due_defaults_setup_users


@pytest.mark.asyncio
async def test_due_tomorrow_uses_defaults_and_returns_slack_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)

    async def _defaults(*, user_id: str):
        _ = user_id
        return TaskDueDefaults(user_id="U1", source="ticktick")

    async def _list_due_tasks(*, due_on, project_ids=None, limit=200):  # noqa: ARG001
        _ = due_on, project_ids, limit
        return [
            SimpleNamespace(
                id="abc12345ff",
                title="Prepare sprint board",
                project_id="P1",
                project_name="tasks",
                due_date="2026-03-02",
            )
        ]

    monkeypatch.setattr(agent._defaults_store, "get_user_defaults", _defaults)
    monkeypatch.setattr(agent._list_manager, "list_due_tasks", _list_due_tasks)

    result = await agent.handle_text(
        TextMessage(content="Which tasks are due tomorrow?", source="U1"),
        _ctx(),
    )

    assert isinstance(result, SlackBlockMessage)
    joined = "\n".join(
        block.get("text", {}).get("text", "")
        for block in result.blocks
        if isinstance(block, dict)
    )
    assert "Prepare sprint board" in joined
    assert "TT-ABC12345" in joined


@pytest.mark.asyncio
async def test_pending_defaults_reply_captures_ticktick_and_returns_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)
    agent._pending_due_defaults_setup_users.add("U1")
    saved_defaults: list[TaskDueDefaults] = []

    async def _upsert(defaults: TaskDueDefaults):
        saved_defaults.append(defaults)
        return True

    async def _projects():
        return [SimpleNamespace(id="P1", name="tasks"), SimpleNamespace(id="P2", name="work")]

    async def _due_tasks(*, due_on, project_ids=None, limit=200):  # noqa: ARG001
        _ = due_on, project_ids, limit
        return []

    monkeypatch.setattr(agent._defaults_store, "upsert_user_defaults", _upsert)
    monkeypatch.setattr(agent._list_manager, "list_projects", _projects)
    monkeypatch.setattr(agent._list_manager, "list_due_tasks", _due_tasks)

    result = await agent.handle_text(
        TextMessage(content="TickTick all lists", source="U1"),
        _ctx(),
    )

    assert isinstance(result, SlackBlockMessage)
    assert saved_defaults
    assert saved_defaults[0].source == "ticktick"
    assert "U1" not in agent._pending_due_defaults_setup_users


@pytest.mark.asyncio
async def test_explicit_defaults_command_works_without_pending_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)
    saved_defaults: list[TaskDueDefaults] = []

    async def _upsert(defaults: TaskDueDefaults):
        saved_defaults.append(defaults)
        return True

    async def _projects():
        return [SimpleNamespace(id="P1", name="tasks")]

    async def _due_tasks(*, due_on, project_ids=None, limit=200):  # noqa: ARG001
        _ = due_on, project_ids, limit
        return []

    monkeypatch.setattr(agent._defaults_store, "upsert_user_defaults", _upsert)
    monkeypatch.setattr(agent._list_manager, "list_projects", _projects)
    monkeypatch.setattr(agent._list_manager, "list_due_tasks", _due_tasks)

    result = await agent.handle_text(
        TextMessage(content="TickTick all lists", source="U1"),
        _ctx(),
    )

    assert isinstance(result, SlackBlockMessage)
    assert saved_defaults
    assert saved_defaults[0].ticktick_project_ids == []


@pytest.mark.asyncio
async def test_defaults_fallback_cache_reused_when_durable_memory_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)
    agent._pending_due_defaults_setup_users.add("U1")

    monkeypatch.setattr(agent._defaults_store, "_ensure_store", lambda: None)

    async def _projects():
        return []

    async def _due_tasks(*, due_on, project_ids=None, limit=200):  # noqa: ARG001
        _ = due_on, project_ids, limit
        return []

    monkeypatch.setattr(agent._list_manager, "list_projects", _projects)
    monkeypatch.setattr(agent._list_manager, "list_due_tasks", _due_tasks)

    first = await agent.handle_text(
        TextMessage(content="TickTick all lists", source="U1"),
        _ctx(),
    )
    second = await agent.handle_text(
        TextMessage(content="Which tasks are due tomorrow?", source="U1"),
        _ctx(),
    )

    assert isinstance(first, SlackBlockMessage)
    assert isinstance(second, SlackBlockMessage)


@pytest.mark.asyncio
async def test_label_edit_updates_title_from_natural_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _build_agent(monkeypatch)

    async def _all_pending():
        return [
            SimpleNamespace(id="abc12345ff", title="Old title", project_id="P1"),
        ]

    async def _update_task_title(*, project_id: str, task_id: str, new_title: str):
        assert project_id == "P1"
        assert task_id == "abc12345ff"
        assert new_title == "New title"
        return True, ""

    monkeypatch.setattr(agent._list_manager, "list_all_pending_tasks", _all_pending)
    monkeypatch.setattr(agent._list_manager, "update_task_title", _update_task_title)

    result = await agent.handle_text(
        TextMessage(content="rename TT-ABC12345 to New title", source="U1"),
        _ctx(),
    )

    assert isinstance(result, TextMessage)
    assert "Updated `TT-ABC12345` title" in result.content
