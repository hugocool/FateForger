"""A read-only facade over the live Notion REST MCP tools for Hugo's board.

The bot's Notion MCP container runs the official server in REST mode: its tools
are ``API-post-database-query``, ``API-retrieve-a-page`` and friends, not the
``notion-search``/``notion-fetch`` pair the older sprint tooling expects. This
module wraps exactly the two read tools and maps the raw REST rows into small
typed models a subagent can hold in its context.

Selection is Notion's own structured filters, always. Nothing here decides which
ticket someone meant by looking at the words in it — that judgement belongs to
the model calling these tools (CLAUDE.md, invariant I1). Truncating a summary by
length is fine; comparing one is not.

Every failure is loud. A missing token, an error envelope, no current sprint, two
current sprints, or a row that is not a page all raise with a message naming the
cause. An empty list here means the board is empty, never that something broke.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel

from fateforger.core.config import settings
from fateforger.tools.notion_mcp import get_notion_mcp_headers, get_notion_mcp_url

# The only two tools this module is ever allowed to call. Both read.
QUERY_TOOL = "API-post-database-query"
RETRIEVE_PAGE_TOOL = "API-retrieve-a-page"

# Notion property names on the two databases. These are schema identifiers, not
# anybody's prose -- the same standing as a SQL column name.
PROP_TASK_ID = "Task ID"
PROP_STATUS = "Status"
PROP_TICKET_STATUS = "Ticket Status"
PROP_PRIORITY = "Priority"
PROP_ESTIMATES = "Estimates"
PROP_DUE = "Due"
PROP_SUMMARY = "Summary"
PROP_DOD = "DoD"
PROP_BLOCKED_BY = "Blocked by"
PROP_PARENT_TASK = "Parent-task"
PROP_PROJECT = "Project"
PROP_SPRINT = "Sprint"
PROP_WORK_TYPE = "Work Type"
PROP_SPRINT_STATUS = "Sprint status"
PROP_DATES = "Dates"

SUMMARY_LIMIT = 200
DOD_LIMIT = 300
PAGE_SIZE_MAX = 100

PRIORITY_SORT: list[dict[str, str]] = [
    {"property": PROP_PRIORITY, "direction": "descending"}
]
CURRENT_SPRINT_FILTER: dict[str, Any] = {
    "property": PROP_SPRINT_STATUS,
    "status": {"equals": "Current"},
}
READY_FILTER: dict[str, Any] = {
    "property": PROP_TICKET_STATUS,
    "select": {"equals": "Ready"},
}
OPEN_FILTER: dict[str, Any] = {
    "and": [
        {"property": PROP_STATUS, "status": {"does_not_equal": "Done"}},
        {"property": PROP_STATUS, "status": {"does_not_equal": "Archived"}},
    ]
}

# The value core/config.py ships when nobody has set a token. Treating it as a
# token would send "change_me..." to Notion and get a 401 nobody can read.
PLACEHOLDER_AUTH_TOKEN = "change_me_to_a_long_random_secret"

Scope = Literal["current_sprint_ready", "current_sprint", "ready", "open"]
CallTool = Callable[[str, dict[str, Any]], Awaitable[str]]


class TaskBoardError(RuntimeError):
    """Anything that stopped the board from answering."""


class TaskBoardUnavailable(TaskBoardError):
    """The board cannot be reached at all — no token, no endpoint."""


class NoCurrentSprint(TaskBoardError):
    """The Sprints database has no page marked Current."""


class AmbiguousCurrentSprint(TaskBoardError):
    """Several sprints claim to be Current; picking one would be a guess."""


class MalformedPage(TaskBoardError):
    """A row came back without the identity a page must have."""


class SprintRef(BaseModel):
    page_id: str
    name: str
    status: str
    start: str | None = None
    end: str | None = None


class TaskRow(BaseModel):
    page_id: str
    number: int | None = None
    name: str
    status: str
    ticket_status: str | None = None
    priority: str | None = None
    estimate: str | None = None
    due: str | None = None
    blocked_by: list[str] = []
    parent_id: str | None = None
    project_id: str | None = None
    work_type: str | None = None
    sprint_id: str | None = None
    summary: str
    dod: str
    url: str
    last_edited: str


class TaskPage(BaseModel):
    """One ticket in full: the row plus untruncated summary and dod."""

    row: TaskRow
    summary: str
    dod: str


class TaskListing(BaseModel):
    scope: str
    sprint: SprintRef | None = None
    tasks: list[TaskRow] = []
    next_cursor: str | None = None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _properties(page: Any) -> dict[str, Any]:
    """The page's properties, or a raised MalformedPage.

    A page with no id has no identity to pass onward, and a page with no
    properties has nothing to map; either one is a broken response, not an
    empty ticket.
    """
    if not isinstance(page, dict):
        raise MalformedPage(f"expected a Notion page object, got {type(page).__name__}")
    page_id = page.get("id")
    if not isinstance(page_id, str) or not page_id:
        raise MalformedPage("Notion page carries no id")
    properties = page.get("properties")
    if not isinstance(properties, dict):
        raise MalformedPage(f"Notion page {page_id} carries no properties")
    return properties


def _text_field(page: Any, key: str) -> str:
    value = page.get(key) if isinstance(page, dict) else None
    return value if isinstance(value, str) else ""


def _plain_text(properties: dict[str, Any], name: str, kind: str) -> str:
    """The concatenated ``plain_text`` of a rich text array."""
    items = _mapping(properties.get(name)).get(kind)
    if not isinstance(items, list):
        return ""
    parts = [_mapping(item).get("plain_text") for item in items]
    return "".join(part for part in parts if isinstance(part, str))


def _title_text(properties: dict[str, Any]) -> str:
    """The page's title, found by property type rather than by name.

    Tasks call it "Task name" and Sprints "Sprint name"; a Notion page has
    exactly one property of type ``title``, and the type is a discriminator
    Notion minted, so keying on it is safe and survives a rename.
    """
    for name, prop in properties.items():
        if _mapping(prop).get("type") == "title":
            return _plain_text(properties, name, "title")
    return ""


def _select_name(properties: dict[str, Any], name: str, kind: str) -> str | None:
    chosen = _mapping(properties.get(name)).get(kind)
    label = _mapping(chosen).get("name")
    return label if isinstance(label, str) else None


def _date_bounds(
    properties: dict[str, Any], name: str
) -> tuple[str | None, str | None]:
    date = _mapping(properties.get(name)).get("date")
    bounds = _mapping(date)
    start = bounds.get("start")
    end = bounds.get("end")
    return (
        start if isinstance(start, str) else None,
        end if isinstance(end, str) else None,
    )


def _relation_ids(properties: dict[str, Any], name: str) -> list[str]:
    related = _mapping(properties.get(name)).get("relation")
    if not isinstance(related, list):
        return []
    return [
        item["id"]
        for item in related
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def _first(values: list[str]) -> str | None:
    return values[0] if values else None


def _unique_number(properties: dict[str, Any], name: str) -> int | None:
    number = _mapping(_mapping(properties.get(name)).get("unique_id")).get("number")
    return number if isinstance(number, int) else None


def _rollup_select_name(properties: dict[str, Any], name: str) -> str | None:
    """The first select in a rollup array — the Work Type a Project rolls up."""
    entries = _mapping(_mapping(properties.get(name)).get("rollup")).get("array")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        label = _mapping(_mapping(entry).get("select")).get("name")
        if isinstance(label, str):
            return label
    return None


def row_from_page(page: Any) -> TaskRow:
    """One Tasks row, with the two long text fields cut to a readable length."""
    properties = _properties(page)
    return TaskRow(
        page_id=page["id"],
        number=_unique_number(properties, PROP_TASK_ID),
        name=_title_text(properties),
        status=_select_name(properties, PROP_STATUS, "status") or "",
        ticket_status=_select_name(properties, PROP_TICKET_STATUS, "select"),
        priority=_select_name(properties, PROP_PRIORITY, "select"),
        estimate=_select_name(properties, PROP_ESTIMATES, "select"),
        due=_date_bounds(properties, PROP_DUE)[0],
        blocked_by=_relation_ids(properties, PROP_BLOCKED_BY),
        parent_id=_first(_relation_ids(properties, PROP_PARENT_TASK)),
        project_id=_first(_relation_ids(properties, PROP_PROJECT)),
        work_type=_rollup_select_name(properties, PROP_WORK_TYPE),
        sprint_id=_first(_relation_ids(properties, PROP_SPRINT)),
        summary=_plain_text(properties, PROP_SUMMARY, "rich_text")[:SUMMARY_LIMIT],
        dod=_plain_text(properties, PROP_DOD, "rich_text")[:DOD_LIMIT],
        url=_text_field(page, "url"),
        last_edited=_text_field(page, "last_edited_time"),
    )


def page_from_page(page: Any) -> TaskPage:
    """One ticket in full: the row, plus the summary and DoD uncut."""
    row = row_from_page(page)
    properties = _properties(page)
    return TaskPage(
        row=row,
        summary=_plain_text(properties, PROP_SUMMARY, "rich_text"),
        dod=_plain_text(properties, PROP_DOD, "rich_text"),
    )


def sprint_from_page(page: Any) -> SprintRef:
    properties = _properties(page)
    start, end = _date_bounds(properties, PROP_DATES)
    return SprintRef(
        page_id=page["id"],
        name=_title_text(properties),
        status=_select_name(properties, PROP_SPRINT_STATUS, "status") or "",
        start=start,
        end=end,
    )


def _decode(text: str) -> dict[str, Any]:
    """The tool's JSON payload, or a raised TaskBoardError naming what came back."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TaskBoardError(
            f"Notion MCP returned a result that is not JSON: {text}"
        ) from exc
    if not isinstance(payload, dict):
        raise TaskBoardError(
            f"Notion MCP returned {type(payload).__name__}, not an object"
        )
    if payload.get("object") == "error":
        message = payload.get("message") or payload.get("code") or text
        raise TaskBoardError(f"Notion API error: {message}")
    return payload


def _results(payload: dict[str, Any]) -> list[Any]:
    rows = payload.get("results")
    return rows if isinstance(rows, list) else []


def _auth_headers() -> dict[str, str]:
    """Bearer headers for the Notion MCP container, or a loud unavailable.

    ``MCP_HTTP_AUTH_TOKEN`` in the environment wins. Settings are the fallback,
    and its placeholder default is not a token: sending it produces a 401 that
    reads like a Notion outage rather than an unconfigured host.
    """
    headers = get_notion_mcp_headers()
    if headers is not None:
        return headers
    token = (settings.mcp_http_auth_token or "").strip()
    if not token or token == PLACEHOLDER_AUTH_TOKEN:
        raise TaskBoardUnavailable(
            "no Notion MCP auth token: set MCP_HTTP_AUTH_TOKEN"
        )
    return {"Authorization": f"Bearer {token}"}


async def notion_call_tool(name: str, arguments: dict[str, Any]) -> str:
    """Call one tool on the Notion MCP server and return its text content."""
    async with streamablehttp_client(
        get_notion_mcp_url(), headers=_auth_headers()
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
    # A REST tool answers in text blocks; anything else (an image, a resource
    # link) carries no JSON and contributes nothing.
    blocks = [getattr(block, "text", None) for block in result.content or []]
    text = "".join(block for block in blocks if isinstance(block, str))
    if result.isError:
        raise TaskBoardError(f"Notion MCP tool {name} failed: {text}")
    return text


class TaskBoard:
    """Reads Hugo's Notion task board through two REST tools and nothing else."""

    def __init__(
        self,
        *,
        call_tool: CallTool,
        tasks_database_id: str,
        sprints_database_id: str,
    ) -> None:
        self._call_tool = call_tool
        self.tasks_database_id = tasks_database_id
        self.sprints_database_id = sprints_database_id

    @classmethod
    def from_settings(cls) -> TaskBoard:
        """A board wired to the live server. Raises now if no token is configured."""
        _auth_headers()
        return cls(
            call_tool=notion_call_tool,
            tasks_database_id=settings.notion_tasks_database_id,
            sprints_database_id=settings.notion_sprints_database_id,
        )

    async def current_sprint(self) -> SprintRef:
        """The one sprint marked Current, or a raised error saying why not one."""
        payload = await self._query(
            self.sprints_database_id, filter_=CURRENT_SPRINT_FILTER, page_size=2
        )
        rows = _results(payload)
        if not rows:
            raise NoCurrentSprint(
                f"no sprint is marked Current in database {self.sprints_database_id}"
            )
        sprints = [sprint_from_page(row) for row in rows]
        if len(sprints) > 1:
            named = " and ".join(sprint.page_id for sprint in sprints)
            raise AmbiguousCurrentSprint(
                f"several sprints are marked Current: {named}"
            )
        return sprints[0]

    async def list_tasks(
        self, scope: Scope, *, limit: int = 25, cursor: str | None = None
    ) -> TaskListing:
        """Tickets in one scope; the two sprint scopes resolve the sprint first."""
        sprint = await self._sprint_for(scope)
        payload = await self._query(
            self.tasks_database_id,
            filter_=self._filter_for(scope, sprint),
            page_size=min(limit, PAGE_SIZE_MAX),
            sorts=PRIORITY_SORT,
            start_cursor=cursor,
        )
        next_cursor = payload.get("next_cursor")
        return TaskListing(
            scope=scope,
            sprint=sprint,
            tasks=[row_from_page(row) for row in _results(payload)],
            # Only when Notion says there is more: a cursor handed back on a
            # last page is how a paginating caller loops forever.
            next_cursor=(
                next_cursor
                if payload.get("has_more") and isinstance(next_cursor, str)
                else None
            ),
        )

    async def get_task(self, page_id: str) -> TaskPage:
        """One ticket in full, by the Notion page id a listing returned."""
        payload = _decode(
            await self._call_tool(RETRIEVE_PAGE_TOOL, {"page_id": page_id})
        )
        return page_from_page(payload)

    async def _sprint_for(self, scope: Scope) -> SprintRef | None:
        if scope in ("current_sprint", "current_sprint_ready"):
            return await self.current_sprint()
        if scope in ("ready", "open"):
            return None
        raise TaskBoardError(
            f"unknown scope {scope!r}: expected one of "
            "current_sprint_ready, current_sprint, ready, open"
        )

    def _filter_for(self, scope: Scope, sprint: SprintRef | None) -> dict[str, Any]:
        if scope == "ready":
            return READY_FILTER
        if scope == "open":
            return OPEN_FILTER
        assert sprint is not None  # _sprint_for resolved it or raised
        in_sprint = {
            "property": PROP_SPRINT,
            "relation": {"contains": sprint.page_id},
        }
        if scope == "current_sprint":
            return in_sprint
        return {"and": [READY_FILTER, in_sprint]}

    async def _query(
        self,
        database_id: str,
        *,
        filter_: dict[str, Any],
        page_size: int,
        sorts: list[dict[str, str]] | None = None,
        start_cursor: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "database_id": database_id,
            "filter": filter_,
        }
        if sorts is not None:
            arguments["sorts"] = sorts
        arguments["page_size"] = page_size
        if start_cursor:
            arguments["start_cursor"] = start_cursor
        return _decode(await self._call_tool(QUERY_TOOL, arguments))


__all__ = [
    "AmbiguousCurrentSprint",
    "CallTool",
    "MalformedPage",
    "NoCurrentSprint",
    "QUERY_TOOL",
    "RETRIEVE_PAGE_TOOL",
    "Scope",
    "SprintRef",
    "TaskBoard",
    "TaskBoardError",
    "TaskBoardUnavailable",
    "TaskListing",
    "TaskPage",
    "TaskRow",
    "notion_call_tool",
    "page_from_page",
    "row_from_page",
    "sprint_from_page",
]
