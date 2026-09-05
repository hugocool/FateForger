"""The stdio MCP server that hands a subagent Hugo's task board, read-only.

Two tools, both reads, over ``agents.tasks.board``. The child that mounts this
picks a ticket to work on; it has no business editing one, so nothing here can
write and there is no third tool to grow into.

Every failure is a failed tool call. A board that could not be reached and a
board with no tickets are opposite answers, and a polite ``{"tasks": []}`` for
the first would have the subagent report the sprint as empty and stop -- the
same silent-wrong-answer shape the facade's loud errors exist to prevent. So a
``TaskBoardError`` propagates and FastMCP turns it into a ``ToolError`` carrying
its message.

The docstrings are short on purpose: this schema is paid for on every turn the
server is mounted, and the instructions above the tools already say the part
that binds the caller.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from fateforger.agents.tasks.board import Scope, TaskBoard

mcp = FastMCP(
    name="task-board",
    instructions=(
        "Read Hugo's Notion task board. Every tool here is read-only; nothing "
        "you call can change a ticket. The `page_id` on each row is a Notion "
        "page id and is the only identity to pass onward -- quote it exactly "
        "when you name a ticket or fetch it in full. If the listing you got "
        "does not contain the ticket you are looking for, list another scope "
        "or say you could not find it; never guess a ticket from its title."
    ),
)

#: Built on first use and kept for the life of the process. That process is not
#: one turn's: dsh-mcp-client starts this child when the plugin activates,
#: supervises it with reconnect, and disposes it with the plugin, so the child
#: outlives any single turn and is respawned on a lost connection. The cache is
#: still safe, because nothing mutable is in it -- two database ids and a
#: function reference. The token and the URL are read afresh inside
#: ``notion_call_tool`` on every call, so rotating either reaches the next tool
#: call without a restart. And ``_CACHED_BOARD`` is assigned only after
#: ``from_settings()`` returns, so a boot with no token retries on the next call
#: rather than caching the failure.
_CACHED_BOARD: TaskBoard | None = None


def _board() -> TaskBoard:
    """The process's board, built lazily so an import cannot need a token."""

    global _CACHED_BOARD
    if _CACHED_BOARD is None:
        _CACHED_BOARD = TaskBoard.from_settings()
    return _CACHED_BOARD


@mcp.tool(name="task_board_list")
async def task_board_list(
    scope: Scope, limit: int = 25, cursor: str | None = None
) -> dict[str, Any]:
    """Tickets in one scope, newest priority first, with the sprint they sit in.

    ``scope`` is ``current_sprint_ready`` (ready work in the current sprint),
    ``current_sprint``, ``ready``, or ``open`` (everything not done or archived).
    """

    listing = await _board().list_tasks(scope, limit=limit, cursor=cursor)
    return listing.model_dump(mode="json")


@mcp.tool(name="task_board_get")
async def task_board_get(page_id: str) -> dict[str, Any]:
    """One ticket in full: its row, plus the summary and DoD untruncated.

    ``page_id`` is the Notion page id from a listing, never a title or a number.
    """

    page = await _board().get_task(page_id)
    return page.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run()


__all__ = ["mcp", "task_board_get", "task_board_list"]
