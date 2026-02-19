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
