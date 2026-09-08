"""The two-tool stdio server a task-picking subagent is given (#241).

Nothing here reaches Notion: every test drives a stub board through the
module's ``_board()`` seam. What is asserted is the *surface* — that exactly
two tools exist and both read, that the arguments the model sends arrive at the
facade unchanged, and that a facade failure reaches the model as a failed tool
call rather than as prose it would read as success.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from fateforger.agents.tasks.board import (
    NoCurrentSprint,
    SprintRef,
    TaskBoard,
    TaskListing,
    TaskPage,
    TaskRow,
)
from fateforger.slack_bot import task_board_mcp

SPRINT_PAGE_ID = "30828174-6a47-80c3-8665-e0755595a48c"
TASK_PAGE_ID = "30828174-6a47-8011-b3d0-000000000001"

TOOL_NAMES = {"task_board_list", "task_board_get"}
SCOPES = ("current_sprint_ready", "current_sprint", "ready", "open")


def _sprint() -> SprintRef:
    return SprintRef(
        page_id=SPRINT_PAGE_ID,
        name="Sprint 8 - product",
        status="Current",
        start="2026-09-01",
        end="2026-09-14",
    )


def _row() -> TaskRow:
    return TaskRow(
        page_id=TASK_PAGE_ID,
        number=500,
        name="Give the subagent a task board",
        status="In progress",
        ticket_status="Ready",
        summary="Two read tools over the board.",
        dod="A subagent can pick a ticket.",
        url="https://www.notion.so/500",
        last_edited="2026-09-05T09:14:00.000Z",
    )


class StubBoard:
    """Records what the tools asked for and answers with canned models."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._error = error

    async def list_tasks(
        self, scope: str, *, limit: int = 25, cursor: str | None = None
    ) -> TaskListing:
        self.calls.append(
            ("list_tasks", {"scope": scope, "limit": limit, "cursor": cursor})
        )
        if self._error is not None:
            raise self._error
        return TaskListing(
            scope=scope, sprint=_sprint(), tasks=[_row()], next_cursor="cursor-2"
        )

    async def get_task(self, page_id: str) -> TaskPage:
        self.calls.append(("get_task", {"page_id": page_id}))
        if self._error is not None:
            raise self._error
        return TaskPage(row=_row(), summary="The whole summary.", dod="The whole DoD.")


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> StubBoard:
    board = StubBoard()
    monkeypatch.setattr(task_board_mcp, "_board", lambda: board)
    return board


# --- the surface --------------------------------------------------------


async def test_exactly_two_tools_are_registered() -> None:
    """A read-only server that grew a third tool would not be one."""
    tools = await task_board_mcp.mcp.list_tools()

    assert {tool.name for tool in tools} == TOOL_NAMES


async def test_tool_names_are_allow_listable() -> None:
    """The harness allow-lists tools by name, so each must match ``[A-Za-z0-9_-]``.

    Characters of an identifier this system minted — no judgement about
    anything a user wrote.
    """
    for tool in await task_board_mcp.mcp.list_tools():
        assert tool.name, "a tool with no name cannot be allow-listed"
        for character in tool.name:
            assert (
                character.isascii() and (character.isalnum() or character in "_-")
            ), f"{tool.name!r} carries {character!r}, which the harness rejects"


async def test_the_scope_vocabulary_reaches_the_model_in_the_schema() -> None:
    """The model can only send a scope it was shown."""
    tools = {tool.name: tool for tool in await task_board_mcp.mcp.list_tools()}
    scope = tools["task_board_list"].inputSchema["properties"]["scope"]

    assert set(scope.get("enum", [])) == set(SCOPES)


def test_the_instructions_say_the_ids_are_the_identity() -> None:
    instructions = task_board_mcp.mcp.instructions or ""

    assert "Notion page id" in instructions
    assert "read-only" in instructions


# --- delegation ---------------------------------------------------------


async def test_list_delegates_scope_limit_cursor(stub: StubBoard) -> None:
    listing = await task_board_mcp.task_board_list(
        scope="current_sprint_ready", limit=5, cursor="cursor-1"
    )

    assert stub.calls == [
        (
            "list_tasks",
            {"scope": "current_sprint_ready", "limit": 5, "cursor": "cursor-1"},
        )
    ]
    assert listing == TaskListing(
        scope="current_sprint_ready",
        sprint=_sprint(),
        tasks=[_row()],
        next_cursor="cursor-2",
    ).model_dump(mode="json")


async def test_list_defaults_to_one_readable_page(stub: StubBoard) -> None:
    await task_board_mcp.task_board_list(scope="ready")

    assert stub.calls == [
        ("list_tasks", {"scope": "ready", "limit": 25, "cursor": None})
    ]


async def test_get_delegates_page_id(stub: StubBoard) -> None:
    page = await task_board_mcp.task_board_get(page_id=TASK_PAGE_ID)

    assert stub.calls == [("get_task", {"page_id": TASK_PAGE_ID})]
    assert page == TaskPage(
        row=_row(), summary="The whole summary.", dod="The whole DoD."
    ).model_dump(mode="json")


async def test_the_board_is_built_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebuilding per call would re-read settings on every tool the model uses."""
    built: list[int] = []

    def _from_settings() -> StubBoard:
        built.append(1)
        return StubBoard()

    monkeypatch.setattr(task_board_mcp, "_CACHED_BOARD", None)
    monkeypatch.setattr(TaskBoard, "from_settings", staticmethod(_from_settings))

    first = task_board_mcp._board()
    second = task_board_mcp._board()

    assert first is second
    assert built == [1]


# --- failure ------------------------------------------------------------


async def test_facade_errors_surface_as_tool_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A polite dict would read to the model as a board with no tickets."""
    cause = NoCurrentSprint("no sprint is marked Current in database 0e34a9da")
    monkeypatch.setattr(task_board_mcp, "_board", lambda: StubBoard(error=cause))

    with pytest.raises(ToolError) as excinfo:
        await task_board_mcp.mcp.call_tool(
            "task_board_list", {"scope": "current_sprint"}
        )

    assert "no sprint is marked Current in database 0e34a9da" in str(excinfo.value)


async def test_a_failed_get_is_a_failed_tool_call_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cause = NoCurrentSprint("Notion page carries no id")
    monkeypatch.setattr(task_board_mcp, "_board", lambda: StubBoard(error=cause))

    with pytest.raises(ToolError) as excinfo:
        await task_board_mcp.mcp.call_tool(
            "task_board_get", {"page_id": TASK_PAGE_ID}
        )

    assert "Notion page carries no id" in str(excinfo.value)


# --- the standing rules -------------------------------------------------


def test_server_module_imports_no_autogen() -> None:
    """This child runs under a stdio harness; it must not drag in the runtime."""
    tree = ast.parse(
        Path(task_board_mcp.__file__).read_text(encoding="utf-8")
    )

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])

    assert "autogen_ext" not in roots
    assert "autogen_agentchat" not in roots
    assert "autogen_core" not in roots
    assert "re" not in roots


def test_importing_the_server_loads_no_autogen_module() -> None:
    """The AST test reads one file; this one watches what the import actually pulls.

    A transitive autogen import costs the stdio child the whole runtime at
    startup, and no source line in this module would show it.
    """
    root = Path(__file__).resolve().parents[2]
    probe = (
        "import sys; import fateforger.slack_bot.task_board_mcp; "
        "print(sorted(n for n in sys.modules "
        "if n.split('.')[0].startswith('autogen')))"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=root,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout
