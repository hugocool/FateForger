import asyncio

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
        list_id=None,
        items=["eggs", "milk"],
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
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
        list_id=None,
        items=["apples", "lemons"],
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
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
        list_id=None,
        items=["Lemons"],
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
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
        list_id=None,
        items=None,
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
    )

    assert result["ok"] is True
    assert len(result["data"]["items"]) == 2
    assert result["data"]["items"][0]["title"] == "Eggs"


@pytest.mark.asyncio
async def test_show_list_items_without_target_returns_all_projects_snapshot():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Work", "PROJECT1"), ("Home", "PROJECT2"))],
            "get_project_tasks": [
                _task_listing("Work", ("T1", "Ship patch")),
                _task_listing("Home", ("T2", "Buy groceries")),
            ],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="show_list_items",
        model="project",
        list_name=None,
        list_id=None,
        items=None,
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
    )

    assert result["ok"] is True
    assert result["data"]["list_name"] == "__all__"
    assert len(result["data"]["items"]) == 2
    assert "across all projects" in result["summary"]


@pytest.mark.asyncio
async def test_show_list_items_without_target_returns_all_items_across_projects():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Work", "PROJECT1"), ("Home", "PROJECT2"))],
            "get_project_tasks": [
                _task_listing(
                    "Work",
                    ("T1", "Task 1"),
                    ("T2", "Task 2"),
                    ("T3", "Task 3"),
                    ("T4", "Task 4"),
                    ("T5", "Task 5"),
                    ("T6", "Task 6"),
                    ("T7", "Task 7"),
                ),
                _task_listing("Home", ("H1", "Home 1"), ("H2", "Home 2")),
            ],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="show_list_items",
        model="project",
        list_name=None,
        list_id=None,
        items=None,
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
    )

    assert result["ok"] is True
    assert result["data"]["list_name"] == "__all__"
    assert len(result["data"]["items"]) == 9


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
        list_id=None,
        item_ids=["T1", "T2"],
        items=["Organic Eggs", "Whole Milk"],
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
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
        list_id=None,
        items=None,
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
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
        list_name=None,
        list_id=None,
        items=None,
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
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
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
    )

    assert result["ok"] is True
    assert result["resolved_list_id"] == "PARENT1"
    assert result["created"] == 3
    assert [name for name, _ in workbench.calls] == [
        "create_task",
        "create_subtask",
        "create_subtask",
    ]


@pytest.mark.asyncio
async def test_list_pending_tasks_returns_empty_when_mcp_is_unavailable():
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=None)

    async def _failing_list_projects():
        raise RuntimeError("endpoint unavailable")

    manager._list_projects = _failing_list_projects  # type: ignore[method-assign]
    rows = await manager.list_pending_tasks(limit=5, per_project_limit=2)

    assert rows == []


@pytest.mark.asyncio
async def test_list_pending_tasks_skips_project_failures_and_returns_remaining_rows():
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=None)

    async def _projects():
        from fateforger.agents.tasks.list_tools import TickTickProject

        return [
            TickTickProject(id="P1", name="Work"),
            TickTickProject(id="P2", name="Home"),
        ]

    async def _project_tasks(project_id: str):
        from fateforger.agents.tasks.list_tools import TickTickTask

        if project_id == "P1":
            raise RuntimeError("project task endpoint failed")
        return [TickTickTask(id="T2", title="Buy groceries", project_id="P2")]

    manager._list_projects = _projects  # type: ignore[method-assign]
    manager._list_project_tasks = _project_tasks  # type: ignore[method-assign]

    rows = await manager.list_pending_tasks(limit=5, per_project_limit=2)

    assert len(rows) == 1
    assert rows[0].id == "T2"
    assert rows[0].project_id == "P2"


@pytest.mark.asyncio
async def test_resolve_ticktick_task_mentions_returns_resolution_states():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Work", "P1"), ("Home", "P2"))],
            "get_project_tasks": [
                _task_listing(
                    "Work",
                    ("T1", "Ship onboarding API"),
                    ("T2", "Refactor auth middleware"),
                ),
                _task_listing(
                    "Home",
                    ("T3", "Buy milk"),
                    ("T4", "Buy coffee beans"),
                ),
            ],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.resolve_ticktick_task_mentions(
        mentions=["Ship onboarding API", "Buy", "Plan vacation"],
        expansion_queries=None,
        max_candidates_per_mention=3,
        min_score=0.45,
        ambiguity_gap=0.08,
        include_all_projects=True,
    )

    assert result["ok"] is True
    assert result["status_counts"] == {"resolved": 1, "ambiguous": 1, "unresolved": 1}
    statuses = {row["mention"]: row["status"] for row in result["results"]}
    assert statuses["Ship onboarding API"] == "resolved"
    assert statuses["Buy"] == "ambiguous"
    assert statuses["Plan vacation"] == "unresolved"
    assert [name for name, _ in workbench.calls] == [
        "get_projects",
        "get_project_tasks",
        "get_project_tasks",
    ]


@pytest.mark.asyncio
async def test_resolve_ticktick_task_mentions_uses_query_expansion():
    workbench = _FakeWorkbench(
        {
            "get_projects": [_project_listing(("Finance", "P1"))],
            "get_project_tasks": [
                _task_listing(
                    "Finance",
                    ("T1", "File taxes 2026"),
                    ("T2", "Renew insurance"),
                )
            ],
        }
    )
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.resolve_ticktick_task_mentions(
        mentions=["tax filing"],
        expansion_queries=["file taxes"],
        max_candidates_per_mention=3,
        min_score=0.45,
        ambiguity_gap=0.08,
        include_all_projects=True,
    )

    assert result["ok"] is True
    row = result["results"][0]
    assert row["status"] == "resolved"
    assert row["resolved_task_id"] == "T1"
    assert row["candidates"][0]["matched_query"] == "file taxes"


@pytest.mark.asyncio
async def test_list_pending_tasks_honors_bounded_parallelism_and_order():
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=None)

    async def _projects():
        from fateforger.agents.tasks.list_tools import TickTickProject

        return [
            TickTickProject(id="P1", name="One"),
            TickTickProject(id="P2", name="Two"),
            TickTickProject(id="P3", name="Three"),
            TickTickProject(id="P4", name="Four"),
        ]

    in_flight = 0
    max_in_flight = 0

    async def _project_tasks(project_id: str):
        from fateforger.agents.tasks.list_tools import TickTickTask

        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return [TickTickTask(id=f"T-{project_id}", title=f"Task {project_id}", project_id=project_id)]

    manager._list_projects = _projects  # type: ignore[method-assign]
    manager._list_project_tasks = _project_tasks  # type: ignore[method-assign]
    manager._pending_snapshot_parallelism = 2  # type: ignore[attr-defined]

    rows = await manager.list_pending_tasks(limit=4, per_project_limit=1)

    assert [row.project_id for row in rows] == ["P1", "P2", "P3", "P4"]
    assert max_in_flight <= 2


@pytest.mark.asyncio
async def test_invalid_input_preserves_parsed_operation_and_model_for_error_metadata():
    workbench = _FakeWorkbench({})
    manager = TickTickListManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.manage_ticktick_lists(
        operation="add_items",
        model="subtask",
        list_name=None,
        list_id=None,
        items=None,
        item_ids=None,
        item_matches=None,
        parent_task_id=None,
        create_if_missing=True,
    )

    assert result["ok"] is False
    assert result["operation"] == "add_items"
    assert result["model"] == "subtask"


def test_extract_first_id_supports_hyphenated_and_underscored_ids():
    assert TickTickListManager._extract_first_id("ID: abc-123_DEF") == "abc-123_DEF"
