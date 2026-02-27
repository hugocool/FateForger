from __future__ import annotations

import json

import pytest

from fateforger.agents.tasks.notion_sprint_tools import NotionSprintManager


class _FakeResult:
    def __init__(self, payload: object):
        self._payload = payload

    def to_text(self) -> str:
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload)


class _FakeWorkbench:
    def __init__(self, responses: dict[str, list[object]]):
        self._responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, *, arguments: dict):
        self.calls.append((name, arguments))
        queue = self._responses.get(name, [])
        if not queue:
            raise RuntimeError(f"No fake response configured for {name}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResult(item)


@pytest.mark.asyncio
async def test_find_sprint_items_supports_query_and_filters():
    workbench = _FakeWorkbench(
        {
            "notion-search": [
                {
                    "results": [
                        {"id": "page-1", "title": "Ship patcher"},
                        {"id": "page-2", "title": "Fix sprint links"},
                    ]
                }
            ]
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.find_sprint_items(
        query="sprint patch",
        data_source_url="collection://sprint-db",
        filters={"status": "WIP"},
        limit=10,
    )

    assert result["ok"] is True
    assert len(result["results"]) == 2
    assert workbench.calls[0][0] == "notion-search"
    assert workbench.calls[0][1]["query"] == "sprint patch"
    assert workbench.calls[0][1]["data_source_url"] == "collection://sprint-db"
    assert workbench.calls[0][1]["filters"] == {"status": "WIP"}


@pytest.mark.asyncio
async def test_find_sprint_items_uses_default_data_source_when_missing():
    workbench = _FakeWorkbench(
        {"notion-search": [{"results": [{"id": "page-1", "title": "Open Ticket"}]}]}
    )
    manager = NotionSprintManager(
        server_url="http://example.invalid/mcp",
        workbench=workbench,
        default_data_source_url="collection://f336d0bc-b841-465b-8045-024475c079dd",
    )

    result = await manager.find_sprint_items(
        query="open tickets",
        data_source_url=None,
        filters=None,
        limit=10,
    )

    assert result["ok"] is True
    assert workbench.calls[0][0] == "notion-search"
    assert (
        workbench.calls[0][1]["data_source_url"]
        == "collection://f336d0bc-b841-465b-8045-024475c079dd"
    )


@pytest.mark.asyncio
async def test_find_sprint_items_resolves_data_source_from_default_database_id():
    workbench = _FakeWorkbench(
        {
            "notion-fetch": [
                '<database>\n<data-source url="collection://f336d0bc-b841-465b-8045-024475c079dd">\n'
            ],
            "notion-search": [{"results": [{"id": "page-1", "title": "Open Ticket"}]}],
        }
    )
    manager = NotionSprintManager(
        server_url="http://example.invalid/mcp",
        workbench=workbench,
        default_database_id="db-page-id",
    )

    result = await manager.find_sprint_items(
        query="open tickets",
        data_source_url=None,
        filters=None,
        limit=10,
    )

    assert result["ok"] is True
    assert [name for name, _ in workbench.calls] == ["notion-fetch", "notion-search"]
    assert (
        workbench.calls[1][1]["data_source_url"]
        == "collection://f336d0bc-b841-465b-8045-024475c079dd"
    )


@pytest.mark.asyncio
async def test_find_sprint_items_returns_error_when_no_source_available():
    manager = NotionSprintManager(
        server_url="http://example.invalid/mcp",
        workbench=_FakeWorkbench({}),
    )

    result = await manager.find_sprint_items(
        query="open tickets",
        data_source_url=None,
        filters=None,
        limit=10,
    )

    assert result["ok"] is False
    assert "No Notion sprint data source is configured" in result["error"]


@pytest.mark.asyncio
async def test_link_sprint_subtasks_updates_each_child_relation():
    workbench = _FakeWorkbench(
        {
            "notion-update-page": ["ok", "ok"],
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.link_sprint_subtasks(
        parent_page_id="parent-1",
        child_page_ids=["child-1", "child-2"],
        relation_property="Parent",
        unlink=False,
    )

    assert result["ok"] is True
    assert result["updated"] == 2
    assert [name for name, _ in workbench.calls] == [
        "notion-update-page",
        "notion-update-page",
    ]

    first_payload = json.loads(workbench.calls[0][1]["data"])
    assert first_payload["page_id"] == "child-1"
    assert first_payload["command"] == "update_properties"
    assert first_payload["properties"]["Parent"] == ["parent-1"]


@pytest.mark.asyncio
async def test_patch_sprint_page_preview_returns_patch_without_writing():
    workbench = _FakeWorkbench(
        {
            "notion-fetch": [
                "# Sprint Notes\n\nStatus: The patient is described as unsteady.\n\nNext block."
            ]
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.patch_sprint_page_content(
        page_id="page-1",
        search_text="The patient is described as unsteady.",
        replace_text="The patient is described as unsteady gait requiring assistance.",
        langdiff_plan_json=None,
        dry_run=True,
        match_threshold=None,
        match_distance=None,
    )

    assert result["ok"] is True
    assert result["mode"] == "preview"
    assert "patch_text" in result
    assert "selection_with_ellipsis" in result
    assert [name for name, _ in workbench.calls] == ["notion-fetch"]


@pytest.mark.asyncio
async def test_patch_sprint_page_conflict_when_anchor_missing():
    workbench = _FakeWorkbench(
        {
            "notion-fetch": ["# Sprint Notes\n\nNothing relevant here."],
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.patch_sprint_page_content(
        page_id="page-1",
        search_text="missing phrase",
        replace_text="replacement",
        langdiff_plan_json=None,
        dry_run=False,
        match_threshold=None,
        match_distance=None,
    )

    assert result["ok"] is False
    assert result["mode"] == "conflict"
    assert "Could not locate" in result["summary"]
    assert [name for name, _ in workbench.calls] == ["notion-fetch"]


@pytest.mark.asyncio
async def test_patch_sprint_page_apply_updates_and_verifies():
    old_text = "# Sprint Notes\n\nStatus: The patient is described as unsteady.\n"
    new_text = "# Sprint Notes\n\nStatus: The patient is described as unsteady gait requiring assistance.\n"
    workbench = _FakeWorkbench(
        {
            "notion-fetch": [old_text, new_text],
            "notion-update-page": ["ok"],
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.patch_sprint_page_content(
        page_id="page-1",
        search_text="The patient is described as unsteady.",
        replace_text="The patient is described as unsteady gait requiring assistance.",
        langdiff_plan_json=None,
        dry_run=False,
        match_threshold=None,
        match_distance=None,
    )

    assert result["ok"] is True
    assert result["mode"] == "applied"
    assert result["verified"] is True
    assert [name for name, _ in workbench.calls] == [
        "notion-fetch",
        "notion-update-page",
        "notion-fetch",
    ]


@pytest.mark.asyncio
async def test_patch_sprint_page_accepts_langdiff_plan_json():
    old_text = "# Sprint Notes\\n\\nStatus: old phrase.\\n"
    new_text = "# Sprint Notes\\n\\nStatus: new phrase.\\n"
    workbench = _FakeWorkbench(
        {
            "notion-fetch": [old_text, new_text],
            "notion-update-page": ["ok"],
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.patch_sprint_page_content(
        page_id="page-1",
        search_text="ignored",
        replace_text="ignored",
        langdiff_plan_json='{"search_text":"old phrase.","replace_text":"new phrase."}',
        dry_run=False,
        match_threshold=None,
        match_distance=None,
    )

    assert result["ok"] is True
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_patch_sprint_event_wraps_single_page_patch():
    workbench = _FakeWorkbench(
        {
            "notion-fetch": [
                "# Sprint Notes\n\nStatus: old phrase.\n",
            ]
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.patch_sprint_event(
        page_id="page-1",
        search_text="old phrase.",
        replace_text="new phrase.",
        langdiff_plan_json=None,
        dry_run=True,
        match_threshold=None,
        match_distance=None,
    )

    assert result["ok"] is True
    assert result["mode"] == "preview"
    assert result["page_id"] == "page-1"
    assert result["result"]["mode"] == "preview"
    assert [name for name, _ in workbench.calls] == ["notion-fetch"]


@pytest.mark.asyncio
async def test_patch_sprint_events_searches_then_patches():
    workbench = _FakeWorkbench(
        {
            "notion-search": [{"results": [{"id": "page-1"}, {"id": "page-2"}]}],
            "notion-fetch": [
                "# Sprint Notes\n\nStatus: old phrase.\n",
                "# Sprint Notes\n\nStatus: old phrase.\n",
            ],
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.patch_sprint_events(
        page_ids=None,
        query="open sprint events",
        data_source_url="collection://sprint-db",
        filters={"status": "WIP"},
        limit=2,
        search_text="old phrase.",
        replace_text="new phrase.",
        langdiff_plan_json=None,
        dry_run=True,
        match_threshold=None,
        match_distance=None,
        stop_on_error=False,
    )

    assert result["ok"] is True
    assert result["selection_mode"] == "search"
    assert result["attempted"] == 2
    assert result["patched"] == 2
    assert result["failed"] == 0
    assert [name for name, _ in workbench.calls] == [
        "notion-search",
        "notion-fetch",
        "notion-fetch",
    ]


@pytest.mark.asyncio
async def test_patch_sprint_events_requires_page_ids_or_query():
    manager = NotionSprintManager(
        server_url="http://example.invalid/mcp",
        workbench=_FakeWorkbench({}),
    )

    result = await manager.patch_sprint_events(
        page_ids=None,
        query=None,
        data_source_url=None,
        filters=None,
        limit=None,
        search_text="old phrase.",
        replace_text="new phrase.",
        langdiff_plan_json=None,
        dry_run=True,
        match_threshold=None,
        match_distance=None,
        stop_on_error=None,
    )

    assert result["ok"] is False
    assert result["selection_mode"] == "none"
    assert result["attempted"] == 0


@pytest.mark.asyncio
async def test_patch_sprint_events_stops_on_first_error_by_default():
    workbench = _FakeWorkbench(
        {
            "notion-search": [{"results": [{"id": "page-1"}, {"id": "page-2"}]}],
            "notion-fetch": [
                "# Sprint Notes\n\nStatus: unrelated text.\n",
            ],
        }
    )
    manager = NotionSprintManager(server_url="http://example.invalid/mcp", workbench=workbench)

    result = await manager.patch_sprint_events(
        page_ids=None,
        query="open sprint events",
        data_source_url="collection://sprint-db",
        filters=None,
        limit=2,
        search_text="old phrase.",
        replace_text="new phrase.",
        langdiff_plan_json=None,
        dry_run=True,
        match_threshold=None,
        match_distance=None,
        stop_on_error=None,
    )

    assert result["ok"] is False
    assert result["attempted"] == 1
    assert result["failed"] == 1
    assert [name for name, _ in workbench.calls] == ["notion-search", "notion-fetch"]


@pytest.mark.asyncio
async def test_find_sprint_items_queries_multiple_default_data_sources():
    source_a = "collection://f336d0bc-b841-465b-8045-024475c079dd"
    source_b = "collection://a5da15f6-b853-455d-8827-f906fb52db2b"
    workbench = _FakeWorkbench(
        {
            "notion-search": [
                {"results": [{"id": "page-1", "title": "Ticket A"}]},
                {"results": [{"id": "page-2", "title": "Ticket B"}]},
            ]
        }
    )
    manager = NotionSprintManager(
        server_url="http://example.invalid/mcp",
        workbench=workbench,
        default_data_source_urls=[source_a, source_b],
    )

    result = await manager.find_sprint_items(
        query="open tickets",
        data_source_url=None,
        filters=None,
        limit=10,
    )

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["data_source_urls"] == [source_a, source_b]
    search_calls = [args for name, args in workbench.calls if name == "notion-search"]
    assert len(search_calls) == 2
    assert search_calls[0]["data_source_url"] == source_a
    assert search_calls[1]["data_source_url"] == source_b


@pytest.mark.asyncio
async def test_find_sprint_items_resolves_multiple_data_sources_from_multiple_databases():
    source_a = "collection://f336d0bc-b841-465b-8045-024475c079dd"
    source_b = "collection://a5da15f6-b853-455d-8827-f906fb52db2b"
    workbench = _FakeWorkbench(
        {
            "notion-fetch": [
                f'<database>\n<data-source url="{source_a}">\n',
                f'<database>\n<data-source url="{source_b}">\n',
            ],
            "notion-search": [
                {"results": [{"id": "page-1", "title": "Ticket A"}]},
                {"results": [{"id": "page-2", "title": "Ticket B"}]},
            ],
        }
    )
    manager = NotionSprintManager(
        server_url="http://example.invalid/mcp",
        workbench=workbench,
        default_database_ids=["db-a", "db-b"],
    )

    result = await manager.find_sprint_items(
        query="open tickets",
        data_source_url=None,
        filters=None,
        limit=10,
    )

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["data_source_urls"] == [source_a, source_b]
    assert [name for name, _ in workbench.calls] == [
        "notion-fetch",
        "notion-fetch",
        "notion-search",
        "notion-search",
    ]
