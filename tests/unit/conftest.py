"""Unit-suite isolation from the live task board.

`TaskBoard.from_settings()` builds a board against the real Notion MCP
container as soon as a token is present, and the host now calls it inside every
candidate planning turn whose session asked for anything. On a machine with no
token — this worktree, and CI — that refuses on its own and every test takes
the loud, non-blocking path by accident. On Hugo's machine, with `.env`
loaded, the same tests would reach Notion over the network.

Isolation that holds only when a credential is missing is not isolation, so it
is asserted here instead: every unit test gets a board that refuses, and a test
that means to exercise the real constructor says so with the `real_task_board`
marker.
"""

from __future__ import annotations

import pytest

from fateforger.agents.tasks.board import TaskBoardUnavailable


@pytest.fixture(autouse=True)
def refuse_the_live_task_board(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No unit test reaches Notion, whatever the environment holds."""

    if request.node.get_closest_marker("real_task_board") is not None:
        return

    def _refuse():
        raise TaskBoardUnavailable("the task board is not reachable from a unit test")

    monkeypatch.setattr(
        "fateforger.agents.tasks.board.TaskBoard.from_settings", staticmethod(_refuse)
    )
