from __future__ import annotations

import pytest

pytest.importorskip("autogen_core")
pytest.importorskip("autogen_agentchat.agents")

from fateforger.agents.tasks import agent as tasks_agent_module


def test_tasks_agent_registers_manage_ticktick_lists_tool(monkeypatch):
    captured: dict[str, object] = {}

    class DummyAssistantAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(tasks_agent_module, "AssistantAgent", DummyAssistantAgent)
    monkeypatch.setattr(tasks_agent_module, "build_autogen_chat_client", lambda _name: object())

    tasks_agent_module.TasksAgent("tasks_agent")

    tools = captured.get("tools")
    assert isinstance(tools, list)
    names = [getattr(tool, "name", "") for tool in tools]
    assert "manage_ticktick_lists" in names
    assert "resolve_ticktick_task_mentions" in names
    assert "find_sprint_items" in names
    assert "link_sprint_subtasks" in names
    assert "patch_sprint_page_content" in names
    assert "patch_sprint_event" in names
    assert "patch_sprint_events" in names
