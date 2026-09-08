"""The read-only Notion board facade (#241).

Every test here drives a fake ``call_tool``: no network, no ``.env``, and the
canned payloads are the shapes the live ``API-post-database-query`` returned on
2026-09-05. What the tests assert is the request the facade *sends* — the
database id, the exact filter JSON, the sort, the page size — because that
request is the whole of the selection logic. Nothing in this package may decide
which ticket matched by looking at its words.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from fateforger.agents.tasks.board import (
    QUERY_TOOL,
    RETRIEVE_PAGE_TOOL,
    AmbiguousCurrentSprint,
    MalformedPage,
    NoCurrentSprint,
    TaskBoard,
    TaskBoardError,
    TaskBoardUnavailable,
    page_from_page,
    row_from_page,
)
from fateforger.core.config import Settings, settings

TASKS_DB = "110baca1-857f-48b1-a8ec-cea325202eef"
SPRINTS_DB = "0e34a9da-1fe6-4cd2-a2fc-c36c0ae688b0"
SPRINT_PAGE_ID = "30828174-6a47-80c3-8665-e0755595a48c"
TASK_PAGE_ID = "30828174-6a47-8011-b3d0-000000000001"

READY_FILTER = {"property": "Ticket Status", "select": {"equals": "Ready"}}
SPRINT_FILTER = {"property": "Sprint", "relation": {"contains": SPRINT_PAGE_ID}}
PRIORITY_SORT = [{"property": "Priority", "direction": "descending"}]

# The value core/config.py ships when nobody has configured a token, taken
# from the field itself so the test cannot drift from what is shipped.
PLACEHOLDER_TOKEN = Settings.model_fields["mcp_http_auth_token"].default


class FakeCallTool:
    """Records each ``(name, arguments)`` and answers the next canned payload."""

    def __init__(self, *payloads: Any) -> None:
        self._payloads = list(payloads)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, dict(arguments)))
        if not self._payloads:
            raise AssertionError(f"unexpected extra call to {name}")
        payload = self._payloads.pop(0)
        return payload if isinstance(payload, str) else json.dumps(payload)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def sprint_page(page_id: str = SPRINT_PAGE_ID) -> dict[str, Any]:
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "last_edited_time": "2026-09-04T18:02:00.000Z",
        "properties": {
            "Sprint name": {
                "id": "title",
                "type": "title",
                "title": [{"type": "text", "plain_text": "Sprint 8 - product"}],
            },
            "Sprint status": {
                "id": "abcd",
                "type": "status",
                "status": {"id": "1", "name": "Current", "color": "blue"},
            },
            "Dates": {
                "id": "efgh",
                "type": "date",
                "date": {
                    "start": "2026-09-01",
                    "end": "2026-09-14",
                    "time_zone": None,
                },
            },
        },
    }


def task_page(
    *,
    page_id: str = TASK_PAGE_ID,
    summary: str = "Wrap the REST tools so a child agent can read the board.",
    dod: str = "Two tools mounted, unit tests green, nothing writes.",
) -> dict[str, Any]:
    """A Tasks row in the shapes ``API-post-database-query`` really returns."""
    return {
        "object": "page",
        "id": page_id,
        "url": "https://www.notion.so/Ship-the-board-facade-30828174",
        "last_edited_time": "2026-09-05T09:14:00.000Z",
        "properties": {
            "Task ID": {
                "id": "unique",
                "type": "unique_id",
                "unique_id": {"prefix": None, "number": 500},
            },
            "Task name": {
                "id": "title",
                "type": "title",
                "title": [
                    {"type": "text", "plain_text": "Ship the board "},
                    {"type": "text", "plain_text": "facade"},
                ],
            },
            "Status": {
                "id": "stat",
                "type": "status",
                "status": {"id": "2", "name": "In progress", "color": "yellow"},
            },
            "Ticket Status": {
                "id": "tick",
                "type": "select",
                "select": {"id": "3", "name": "Ready", "color": "green"},
            },
            "Priority": {
                "id": "prio",
                "type": "select",
                "select": {"id": "4", "name": "High", "color": "red"},
            },
            "Estimates": {
                "id": "esti",
                "type": "select",
                "select": {"id": "5", "name": "M", "color": "gray"},
            },
            "Due": {
                "id": "due",
                "type": "date",
                "date": {"start": "2026-09-08", "end": None, "time_zone": None},
            },
            "Summary": {
                "id": "summ",
                "type": "rich_text",
                "rich_text": [{"type": "text", "plain_text": summary}],
            },
            "DoD": {
                "id": "dod",
                "type": "rich_text",
                "rich_text": [{"type": "text", "plain_text": dod}],
            },
            "Blocked by": {
                "id": "block",
                "type": "relation",
                "relation": [{"id": "blocked-page-1"}, {"id": "blocked-page-2"}],
                "has_more": False,
            },
            "Parent-task": {
                "id": "parent",
                "type": "relation",
                "relation": [{"id": "parent-page-1"}],
                "has_more": False,
            },
            "Project": {
                "id": "proj",
                "type": "relation",
                "relation": [{"id": "project-page-1"}],
                "has_more": False,
            },
            "Sprint": {
                "id": "sprint",
                "type": "relation",
                "relation": [{"id": SPRINT_PAGE_ID}],
                "has_more": False,
            },
            "Work Type": {
                "id": "work",
                "type": "rollup",
                "rollup": {
                    "type": "array",
                    "array": [{"type": "select", "select": {"name": "Product"}}],
                    "function": "show_original",
                },
            },
            "Output Artifact": {"id": "art", "type": "url", "url": None},
        },
    }


def listing(*pages: dict[str, Any], next_cursor: str | None = None) -> dict[str, Any]:
    return {
        "object": "list",
        "results": list(pages),
        "has_more": next_cursor is not None,
        "next_cursor": next_cursor,
    }


def board(call_tool: FakeCallTool) -> TaskBoard:
    return TaskBoard(
        call_tool=call_tool,
        tasks_database_id=TASKS_DB,
        sprints_database_id=SPRINTS_DB,
    )


# --- the current sprint -------------------------------------------------


async def test_current_sprint_filters_on_status_current() -> None:
    fake = FakeCallTool(listing(sprint_page()))

    sprint = await board(fake).current_sprint()

    name, arguments = fake.calls[0]
    assert name == QUERY_TOOL
    assert arguments == {
        "database_id": SPRINTS_DB,
        "filter": {"property": "Sprint status", "status": {"equals": "Current"}},
        "page_size": 2,
    }
    assert sprint.page_id == SPRINT_PAGE_ID
    assert sprint.name == "Sprint 8 - product"
    assert sprint.status == "Current"
    assert sprint.start == "2026-09-01"
    assert sprint.end == "2026-09-14"


async def test_no_current_sprint_raises() -> None:
    fake = FakeCallTool(listing())

    with pytest.raises(NoCurrentSprint) as excinfo:
        await board(fake).current_sprint()

    assert SPRINTS_DB in str(excinfo.value)


async def test_two_current_sprints_raise_naming_both() -> None:
    other = "30828174-6a47-80c3-8665-e0755595aaaa"
    fake = FakeCallTool(listing(sprint_page(), sprint_page(other)))

    with pytest.raises(AmbiguousCurrentSprint) as excinfo:
        await board(fake).current_sprint()

    message = str(excinfo.value)
    assert SPRINT_PAGE_ID in message
    assert other in message


# --- scopes -------------------------------------------------------------


async def test_ready_scope_sends_the_ticket_status_filter() -> None:
    fake = FakeCallTool(listing(task_page()))

    result = await board(fake).list_tasks("ready", limit=10)

    assert fake.calls == [
        (
            QUERY_TOOL,
            {
                "database_id": TASKS_DB,
                "filter": READY_FILTER,
                "sorts": PRIORITY_SORT,
                "page_size": 10,
            },
        )
    ]
    assert result.scope == "ready"
    assert result.sprint is None
    assert [row.page_id for row in result.tasks] == [TASK_PAGE_ID]


async def test_current_sprint_ready_combines_both_filters_under_and() -> None:
    fake = FakeCallTool(listing(sprint_page()), listing(task_page()))

    result = await board(fake).list_tasks("current_sprint_ready")

    assert fake.calls[1] == (
        QUERY_TOOL,
        {
            "database_id": TASKS_DB,
            "filter": {"and": [READY_FILTER, SPRINT_FILTER]},
            "sorts": PRIORITY_SORT,
            "page_size": 25,
        },
    )
    assert result.sprint is not None
    assert result.sprint.page_id == SPRINT_PAGE_ID


async def test_current_sprint_scope_sends_only_the_relation_filter() -> None:
    fake = FakeCallTool(listing(sprint_page()), listing(task_page()))

    result = await board(fake).list_tasks("current_sprint")

    assert fake.calls[1][1]["filter"] == SPRINT_FILTER
    assert result.sprint is not None


async def test_open_scope_excludes_done_and_archived() -> None:
    fake = FakeCallTool(listing(task_page()))

    result = await board(fake).list_tasks("open")

    assert fake.calls[0][1]["filter"] == {
        "and": [
            {"property": "Status", "status": {"does_not_equal": "Done"}},
            {"property": "Status", "status": {"does_not_equal": "Archived"}},
        ]
    }
    assert result.sprint is None


async def test_page_size_is_capped_at_the_notion_maximum() -> None:
    fake = FakeCallTool(listing(), listing())

    await board(fake).list_tasks("ready", limit=500)
    await board(fake).list_tasks("ready", limit=0)

    assert fake.calls[0][1]["page_size"] == 100
    assert fake.calls[1][1]["page_size"] == 1, "Notion rejects page_size 0"


async def test_unknown_scope_raises_rather_than_listing_everything() -> None:
    fake = FakeCallTool()

    with pytest.raises(TaskBoardError) as excinfo:
        await board(fake).list_tasks("whatever")  # type: ignore[arg-type]

    assert "whatever" in str(excinfo.value)
    assert fake.calls == []


async def test_cursor_is_forwarded_and_next_cursor_returned() -> None:
    fake = FakeCallTool(listing(task_page(), next_cursor="cursor-2"))

    result = await board(fake).list_tasks("ready", cursor="cursor-1")

    assert fake.calls[0][1]["start_cursor"] == "cursor-1"
    assert result.next_cursor == "cursor-2"


async def test_a_last_page_reports_no_cursor() -> None:
    fake = FakeCallTool(listing(task_page()))

    result = await board(fake).list_tasks("ready")

    assert "start_cursor" not in fake.calls[0][1]
    assert result.next_cursor is None


async def test_only_the_two_read_tools_are_ever_called() -> None:
    fake = FakeCallTool(
        listing(sprint_page()),
        listing(task_page()),
        task_page(),
    )
    subject = board(fake)

    await subject.list_tasks("current_sprint_ready")
    await subject.get_task(TASK_PAGE_ID)

    assert set(fake.names) <= {QUERY_TOOL, RETRIEVE_PAGE_TOOL}


async def test_get_task_retrieves_the_page_by_id() -> None:
    fake = FakeCallTool(task_page())

    page = await board(fake).get_task(TASK_PAGE_ID)

    assert fake.calls == [(RETRIEVE_PAGE_TOOL, {"page_id": TASK_PAGE_ID})]
    assert page.row.page_id == TASK_PAGE_ID


# --- row mapping --------------------------------------------------------


def test_row_from_page_maps_every_field() -> None:
    row = row_from_page(task_page())

    assert row.page_id == TASK_PAGE_ID
    assert row.number == 500
    assert row.name == "Ship the board facade"
    assert row.status == "In progress"
    assert row.ticket_status == "Ready"
    assert row.priority == "High"
    assert row.estimate == "M"
    assert row.due == "2026-09-08"
    assert row.blocked_by == ["blocked-page-1", "blocked-page-2"]
    assert row.parent_id == "parent-page-1"
    assert row.project_id == "project-page-1"
    assert row.work_type == "Product"
    assert row.sprint_id == SPRINT_PAGE_ID
    assert row.summary.startswith("Wrap the REST tools")
    assert row.dod.startswith("Two tools mounted")
    assert row.url == "https://www.notion.so/Ship-the-board-facade-30828174"
    assert row.last_edited == "2026-09-05T09:14:00.000Z"


def test_row_from_page_tolerates_missing_properties() -> None:
    row = row_from_page({"id": TASK_PAGE_ID, "properties": {}})

    assert row.page_id == TASK_PAGE_ID
    assert row.number is None
    assert row.name == ""
    assert row.status == ""
    assert row.ticket_status is None
    assert row.priority is None
    assert row.estimate is None
    assert row.due is None
    assert row.blocked_by == []
    assert row.parent_id is None
    assert row.project_id is None
    assert row.work_type is None
    assert row.sprint_id is None
    assert row.summary == ""
    assert row.dod == ""
    assert row.url == ""
    assert row.last_edited == ""


def test_an_empty_rollup_and_a_null_date_map_to_none() -> None:
    page = task_page()
    page["properties"]["Work Type"]["rollup"]["array"] = []
    page["properties"]["Due"]["date"] = None

    row = row_from_page(page)

    assert row.work_type is None
    assert row.due is None


def test_summary_and_dod_are_truncated_in_rows_but_full_in_page() -> None:
    long_summary = "s" * 250
    long_dod = "d" * 400
    page = task_page(summary=long_summary, dod=long_dod)

    row = row_from_page(page)
    full = page_from_page(page)

    assert len(row.summary) == 200
    assert len(row.dod) == 300
    assert full.summary == long_summary
    assert full.dod == long_dod
    assert full.row.summary == row.summary


@pytest.mark.parametrize(
    "page",
    [
        {"properties": {}},
        {"id": TASK_PAGE_ID},
        {"id": TASK_PAGE_ID, "properties": "not a mapping"},
        "not a page at all",
    ],
)
def test_a_page_without_id_or_properties_raises(page: Any) -> None:
    with pytest.raises(MalformedPage):
        row_from_page(page)


# --- loud failures ------------------------------------------------------


async def test_error_envelope_raises_with_the_server_message() -> None:
    fake = FakeCallTool(
        {
            "object": "error",
            "status": 400,
            "code": "validation_error",
            "message": "Could not find sort property with name or id: Priority",
        }
    )

    with pytest.raises(TaskBoardError) as excinfo:
        await board(fake).list_tasks("ready")

    assert "Could not find sort property" in str(excinfo.value)


async def test_a_non_json_result_raises_rather_than_returning_nothing() -> None:
    fake = FakeCallTool("<html>502 Bad Gateway</html>")

    with pytest.raises(TaskBoardError) as excinfo:
        await board(fake).list_tasks("ready")

    assert "502 Bad Gateway" in str(excinfo.value)


def test_missing_token_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HTTP_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(settings, "mcp_http_auth_token", PLACEHOLDER_TOKEN)

    with pytest.raises(TaskBoardUnavailable) as excinfo:
        TaskBoard.from_settings()

    assert "token" in str(excinfo.value)


def test_placeholder_token_in_env_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.env.template` ships the placeholder, so it arrives by environment too.

    Settings are untouched here: the environment wins, and forwarding
    "change_me..." to Notion buys a 401 that reads like an outage rather than
    like a host nobody configured.
    """
    monkeypatch.setenv("MCP_HTTP_AUTH_TOKEN", PLACEHOLDER_TOKEN)

    with pytest.raises(TaskBoardUnavailable) as excinfo:
        TaskBoard.from_settings()

    assert "token" in str(excinfo.value)


def test_from_settings_uses_the_configured_database_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_HTTP_AUTH_TOKEN", "a-real-looking-secret")

    subject = TaskBoard.from_settings()

    assert subject.tasks_database_id == settings.notion_tasks_database_id
    assert subject.sprints_database_id == settings.notion_sprints_database_id


def test_settings_carry_the_board_database_ids() -> None:
    assert settings.notion_tasks_database_id == TASKS_DB
    assert settings.notion_sprints_database_id == SPRINTS_DB


# --- the standing rule --------------------------------------------------


def test_board_uses_no_pattern_matching() -> None:
    """No `re`, no `difflib`: selection is Notion's structured filters only."""
    from fateforger.agents.tasks import board as board_module

    source = Path(board_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])

    assert "re" not in imported
    assert "difflib" not in imported
