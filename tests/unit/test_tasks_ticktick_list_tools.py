import pytest

from fateforger.agents.tasks.list_tools import TickTickListManager


class _FakeResult:
    def __init__(self, text: str):
        self._text = text

    def to_text(self) -> str:
        return self._text


class _FakeWorkbench:
    def __init__(self, responses: dict[str, list[object]]):
        self._responses = {key: list(value) for key, value in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, *, arguments: dict):
        self.calls.append((name, arguments))
        queue = self._responses.get(name, [])
        if not queue:
            raise RuntimeError(f"No fake response configured for {name}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResult(str(item))


def _project_listing(*rows: tuple[str, str]) -> str:
    out = [f"Found {len(rows)} projects:", ""]
    for index, (name, project_id) in enumerate(rows, start=1):
        out.extend(
            [
                f"Project {index}:",
                f"Name: {name}",
                f"ID: {project_id}",
                "Kind: TASK",
                "",
            ]
        )
    return "\n".join(out).strip()


def _task_listing(project_name: str, *rows: tuple[str, str]) -> str:
    out = [f"Found {len(rows)} tasks in project '{project_name}':", ""]
    for index, (task_id, title) in enumerate(rows, start=1):
        out.extend(
            [
                f"Task {index}:",
                f"ID: {task_id}",
                f"Title: {title}",
                "Project ID: PROJECT1",
                "",
            ]
        )
    return "\n".join(out).strip()


@pytest.mark.asyncio
async def test_create_list_with_items_uses_project_model():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing()],
            "create_project": ["Project created successfully:\nName: Weekend Prep\nID: PROJECT1"],
            "batch_create_tasks": ["Successfully created: 2 tasks\nFailed: 0 tasks"],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="create_list",
        model="project",
        list_name="Weekend Prep",
        items=["eggs", "milk"],
    )

    assert result["ok"] is True
    assert result["resolved_list_id"] == "PROJECT1"
    assert result["created"] == 2
    assert [name for name, _ in workbench.calls] == [
        "get_projects",
        "create_project",
        "batch_create_tasks",
    ]


@pytest.mark.asyncio
async def test_add_items_to_existing_list():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Weekend Prep", "PROJECT1"))],
            "batch_create_tasks": ["Successfully created: 2 tasks\nFailed: 0 tasks"],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="add_items",
        model="project",
        list_name="Weekend Prep",
        items=["apples", "lemons"],
    )

    assert result["ok"] is True
    assert result["created"] == 2
    assert result["resolved_list_id"] == "PROJECT1"
    assert [name for name, _ in workbench.calls] == ["get_projects", "batch_create_tasks"]


@pytest.mark.asyncio
async def test_remove_items_returns_ambiguity_without_deleting():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Weekend Prep", "PROJECT1"))],
            "get_project_tasks": [
                _task_listing("Weekend Prep", ("T1", "Lemons"), ("T2", "Lemons"))
            ],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="remove_items",
        model="project",
        list_name="Weekend Prep",
        items=["Lemons"],
    )

    assert result["ok"] is False
    assert result["deleted"] == 0
    assert any("multiple items" in error.lower() for error in result["errors"])
    assert [name for name, _ in workbench.calls] == ["get_projects", "get_project_tasks"]


@pytest.mark.asyncio
async def test_show_list_items_returns_items():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Weekend Prep", "PROJECT1"))],
            "get_project_tasks": [_task_listing("Weekend Prep", ("T1", "Eggs"), ("T2", "Milk"))],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="show_list_items",
        model="project",
        list_name="Weekend Prep",
    )

    assert result["ok"] is True
    assert len(result["data"]["items"]) == 2
    assert result["data"]["items"][0]["title"] == "Eggs"


@pytest.mark.asyncio
async def test_update_items_uses_item_ids():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Weekend Prep", "PROJECT1"))],
            "update_task": ["ok", "ok"],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="update_items",
        model="project",
        list_name="Weekend Prep",
        item_ids=["T1", "T2"],
        items=["Organic Eggs", "Whole Milk"],
    )

    assert result["ok"] is True
    assert result["updated"] == 2
    assert [name for name, _ in workbench.calls] == ["get_projects", "update_task", "update_task"]


@pytest.mark.asyncio
async def test_delete_list_deletes_project():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Weekend Prep", "PROJECT1"))],
            "delete_project": ["deleted"],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="delete_list",
        model="project",
        list_name="Weekend Prep",
    )

    assert result["ok"] is True
    assert result["deleted"] == 1
    assert [name for name, _ in workbench.calls] == ["get_projects", "delete_project"]


@pytest.mark.asyncio
async def test_returns_concise_error_when_mcp_call_fails():
    workbench = _FakeWorkbench({"get_projects": [RuntimeError("boom")]})
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="show_lists",
        model="project",
    )

    assert result["ok"] is False
    assert "TickTick MCP failure" in result["summary"]


@pytest.mark.asyncio
async def test_subtask_create_list_and_add_items():
    workbench = _FakeWorkbench(
        {
            "create_task": ["Task created successfully:\nID: PARENT1"],
            "create_subtask": ["ok", "ok"],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="create_list",
        model="subtask",
        list_id="PROJECT1",
        list_name="Pack",
        items=["Socks", "Charger"],
    )

    assert result["ok"] is True
    assert result["resolved_list_id"] == "PARENT1"
    assert result["created"] == 3
    assert [name for name, _ in workbench.calls] == [
        "create_task",
        "create_subtask",
        "create_subtask",
    ]
