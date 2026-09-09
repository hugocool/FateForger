"""The TaskSource port and the board adapter behind it (#401).

No network and no model call: every test drives a fake board that answers one
canned ``TaskListing`` or raises. What is asserted is the mapping -- which GTD
state a row lands in, whether it is overdue on the day being planned, what a
cursor and a resolved sprint mean for the listing as a whole -- and that a
board which fails becomes one named failure carrying its cause, never an empty
list standing in for an error.

The state mapping compares against Notion's own enum strings ("Done",
"Blocked", "Ready"): vocabulary the board minted, which is the identifier case
CLAUDE.md holds outside the no-matching rule. Nothing here reads a ticket's
prose to decide anything.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from fateforger.agents.tasks.board import (
    Scope,
    SprintRef,
    TaskBoard,
    TaskBoardError,
    TaskListing,
    TaskRow,
)
from fateforger.agents.tasks.task_source import (
    BoardTaskSource,
    TaskSource,
    TaskSourceUnavailable,
)

DAY = date(2026, 9, 9)
TASK_PAGE_ID = "30828174-6a47-8011-b3d0-000000000001"
OTHER_PAGE_ID = "30828174-6a47-8011-b3d0-000000000002"
SPRINT_PAGE_ID = "30828174-6a47-80c3-8665-e0755595a48c"


def row(
    *,
    page_id: str = TASK_PAGE_ID,
    number: int | None = 500,
    name: str = "Ship the board facade",
    status: str = "In progress",
    ticket_status: str | None = "Ready",
    due: str | None = None,
    blocked_by: list[str] | None = None,
    summary: str = "",
    url: str | None = None,
) -> TaskRow:
    """A board row, with only the fields the port reads left to vary."""
    return TaskRow(
        page_id=page_id,
        number=number,
        name=name,
        status=status,
        ticket_status=ticket_status,
        due=due,
        blocked_by=list(blocked_by or []),
        summary=summary,
        dod="",
        url=url if url is not None else f"https://www.notion.so/{page_id}",
        last_edited="2026-09-08T09:14:00.000Z",
    )


def sprint_ref(name: str = "Sprint 8 - product") -> SprintRef:
    return SprintRef(page_id=SPRINT_PAGE_ID, name=name, status="Current")


def listing(
    *rows: TaskRow,
    sprint: SprintRef | None = None,
    next_cursor: str | None = None,
) -> TaskListing:
    return TaskListing(
        scope="current_sprint_ready",
        sprint=sprint,
        tasks=list(rows),
        next_cursor=next_cursor,
    )


class FakeBoard:
    """Answers one canned listing, or raises it, and records how it was asked.

    `list_tasks` carries `TaskBoard`'s signature exactly, which
    `test_the_port_and_its_fake_match_what_they_stand_for` holds it to: a fake
    accepting a call the real board refuses would let this file pin an
    interface nothing implements.
    """

    def __init__(self, answer: TaskListing | Exception) -> None:
        self._answer = answer
        self.calls: list[tuple[str, int]] = []

    async def list_tasks(
        self, scope: Scope, *, limit: int = 25, cursor: str | None = None
    ) -> TaskListing:
        self.calls.append((scope, limit))
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


# --- the state mapping --------------------------------------------------


@pytest.mark.parametrize(
    "status, ticket_status, expected",
    [
        ("In progress", "Ready", "next"),
        ("In progress", "Refined", "next"),
        ("In progress", "Blocked", "waiting_for"),
        ("In progress", "Paused", "someday"),
        ("In progress", "Zombie", "someday"),
        # Unrefined has no next action yet, so it is not something the planner
        # may treat as ready; the scope keeps it off the board anyway.
        ("In progress", "Unrefined", "someday"),
        ("In progress", None, "someday"),
        ("Done", "Ready", "done"),
        ("Archived", "Ready", "done"),
    ],
)
async def test_state_follows_the_boards_own_enums(
    status: str, ticket_status: str | None, expected: str
) -> None:
    fake = FakeBoard(listing(row(status=status, ticket_status=ticket_status)))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert [candidate.state for candidate in result.rows] == [expected]


async def test_a_finished_ticket_is_done_even_while_it_reads_as_blocked() -> None:
    """Status is asked first, and that ordering is the whole of the answer.

    A finished ticket keeps whatever Ticket Status it was last left at, so a
    mapping that consulted Ticket Status first would call a closed ticket
    `waiting_for` and offer the planner a row nobody can act on.
    """
    fake = FakeBoard(listing(row(status="Done", ticket_status="Blocked")))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.rows[0].state == "done"


# --- the day's arithmetic -----------------------------------------------


@pytest.mark.parametrize(
    "due, expected_due, expected_overdue",
    [
        ("2026-09-08", date(2026, 9, 8), True),
        ("2026-09-10", date(2026, 9, 10), False),
        # The boundary: a ticket due on the day being planned is due, not late.
        ("2026-09-09", date(2026, 9, 9), False),
        (None, None, False),
    ],
)
async def test_overdue_is_arithmetic_against_the_day_being_planned(
    due: str | None, expected_due: date | None, expected_overdue: bool
) -> None:
    fake = FakeBoard(listing(row(due=due)))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.rows[0].due == expected_due
    assert result.rows[0].overdue is expected_overdue


async def test_a_due_carrying_a_time_keeps_its_date() -> None:
    """Notion's Due is a date property, and a date property may carry a time."""
    fake = FakeBoard(listing(row(due="2026-09-08T17:00:00.000+02:00")))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.rows[0].due == date(2026, 9, 8)
    assert result.rows[0].overdue is True


# --- the listing --------------------------------------------------------


async def test_a_cursor_says_the_listing_was_truncated() -> None:
    fake = FakeBoard(listing(row(), next_cursor="cursor-2"))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.truncated is True


async def test_a_last_page_is_not_truncated() -> None:
    fake = FakeBoard(listing(row()))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.truncated is False


async def test_the_sprints_name_heads_the_listing() -> None:
    fake = FakeBoard(listing(row(), sprint=sprint_ref()))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.sprint == "Sprint 8 - product"


async def test_a_scope_that_resolved_no_sprint_names_none() -> None:
    fake = FakeBoard(listing(row()))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.sprint is None


async def test_the_candidates_carry_the_day_they_were_read_for() -> None:
    fake = FakeBoard(listing(row()))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.day == DAY


async def test_identity_comes_from_the_board_and_is_never_minted_here() -> None:
    fake = FakeBoard(
        listing(
            row(
                page_id=TASK_PAGE_ID,
                number=500,
                name="Ship the board facade",
                blocked_by=["blocked-page-1", "blocked-page-2"],
            )
        )
    )

    result = await BoardTaskSource(fake).candidates(DAY)

    candidate = result.rows[0]
    assert candidate.source == "notion"
    assert candidate.external_id == TASK_PAGE_ID
    assert candidate.number == 500
    assert candidate.label == "Ship the board facade"
    assert candidate.blocked_by == ["blocked-page-1", "blocked-page-2"]


async def test_the_summary_and_the_url_are_carried_for_their_one_reader_each() -> None:
    """Two board facts the port carries because a downstream half needs them.

    The summary is the tail of the line `work_lookup.build_prompt` renders,
    and the eval's measured rates were taken with it there
    (`tests/integration/test_eval_work_lookup.py`): a candidate without one
    would silently change the prompt text and move a measured judgement. The
    url is what the material store is given when a resolved ticket is written
    (`timeboxing_host._store_materials`); deriving one from a page id here
    would be minting, and a handle backed by a link nobody can follow is worse
    than no handle.

    Neither is read by anything in this module, and neither decides anything.
    """
    fake = FakeBoard(
        listing(
            row(
                summary="Check the corporate tax return before it is filed.",
                url="https://www.notion.so/Ship-the-board-facade-30828174",
            )
        )
    )

    result = await BoardTaskSource(fake).candidates(DAY)

    candidate = result.rows[0]
    assert candidate.summary == "Check the corporate tax return before it is filed."
    assert candidate.url == "https://www.notion.so/Ship-the-board-facade-30828174"


async def test_a_ticket_with_no_number_keeps_none() -> None:
    fake = FakeBoard(listing(row(number=None)))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.rows[0].number is None


async def test_rows_keep_the_boards_order() -> None:
    """The order is the person's own ranking, and the judgement is told so."""
    fake = FakeBoard(
        listing(
            row(page_id=TASK_PAGE_ID, number=500),
            row(page_id=OTHER_PAGE_ID, number=42),
        )
    )

    result = await BoardTaskSource(fake).candidates(DAY)

    assert [candidate.number for candidate in result.rows] == [500, 42]


async def test_an_empty_board_is_an_empty_listing_and_not_a_failure() -> None:
    fake = FakeBoard(listing(sprint=sprint_ref()))

    result = await BoardTaskSource(fake).candidates(DAY)

    assert result.rows == []
    assert result.sprint == "Sprint 8 - product"


# --- what reaches the board ---------------------------------------------


async def test_the_scope_and_limit_reach_the_board_unchanged() -> None:
    default_board = FakeBoard(listing())
    widened_board = FakeBoard(listing())

    await BoardTaskSource(default_board).candidates(DAY)
    await BoardTaskSource(widened_board, scope="open").candidates(DAY, limit=3)

    assert default_board.calls == [("current_sprint_ready", 12)]
    assert widened_board.calls == [("open", 3)]


# --- loud failures ------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        TaskBoardError("Notion API error: could not find sort property"),
        # The likeliest real failure: an outage arrives from inside the MCP
        # client, typed as nothing this package knows.
        RuntimeError("connection reset by peer"),
    ],
)
async def test_a_board_that_fails_becomes_one_named_failure_with_its_cause(
    error: Exception,
) -> None:
    fake = FakeBoard(error)

    with pytest.raises(TaskSourceUnavailable) as excinfo:
        await BoardTaskSource(fake).candidates(DAY)

    assert excinfo.value.__cause__ is error


async def test_a_due_the_board_wrote_but_nobody_can_parse_fails_loudly() -> None:
    """The mapping is inside the guard, and this is why.

    Dropping a deadline that will not parse would turn an overdue ticket into
    a ticket that is not overdue -- a silent wrong answer of exactly the shape
    the caller's unresolved flag exists to prevent. With the mapping outside
    the `try` this escapes as a bare `ValueError`, past every caller that
    handles `TaskSourceUnavailable` and only that.
    """
    fake = FakeBoard(listing(row(due="sometime next week")))

    with pytest.raises(TaskSourceUnavailable) as excinfo:
        await BoardTaskSource(fake).candidates(DAY)

    assert isinstance(excinfo.value.__cause__, ValueError)


# --- the port's shape ---------------------------------------------------


def test_the_port_and_its_fake_match_what_they_stand_for() -> None:
    """Two stand-ins, held to the things they stand in for.

    `BoardTaskSource` is handed around as a `TaskSource` and `FakeBoard` in
    place of a `TaskBoard`; neither relationship is checked at runtime, so a
    renamed method or a moved default would surface only in the host, a task
    later. Signatures are compared whole -- names, kinds, defaults, the
    keyword-only marker and annotations.

    `eval_str=True` because the interface is the types, not how they are
    spelled: every module here carries `from __future__ import annotations`, so
    unevaluated the annotations are source strings and this would fail over
    `Scope` written out as its `Literal`.
    """
    assert inspect.signature(
        BoardTaskSource.candidates, eval_str=True
    ) == inspect.signature(
        TaskSource.candidates, eval_str=True
    ), "BoardTaskSource has drifted from the TaskSource port"

    assert inspect.signature(
        FakeBoard.list_tasks, eval_str=True
    ) == inspect.signature(
        TaskBoard.list_tasks, eval_str=True
    ), "FakeBoard has drifted from TaskBoard.list_tasks"


# --- the standing rule --------------------------------------------------


def test_the_port_uses_no_pattern_matching() -> None:
    """No `re`, no `difflib`: the mapping is over enums the board minted."""
    from fateforger.agents.tasks import task_source as task_source_module

    source = Path(task_source_module.__file__).read_text(encoding="utf-8")
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


def test_the_port_does_not_pull_the_board_in_at_import() -> None:
    """`board` reaches out at import time -- it pulls the MCP streamable-http
    client and constructs a `Settings()` at module scope -- and this module is
    imported by `session_contracts`, which the per-turn stdio children load.

    Importing it eagerly cost 1327ms against 143ms and made a pure contracts
    module transitively require the MCP client package and a constructible
    config in order to load at all (#417). Nothing here needs those names at
    runtime, so they sit behind `TYPE_CHECKING`.

    Asserted in a fresh interpreter over `sys.modules`, because this suite has
    already imported `board` for its own fakes; module names are identifiers
    this system minted, not anyone's prose.
    """

    probe = (
        "import sys;"
        "import fateforger.agents.tasks.task_source;"
        "print('fateforger.agents.tasks.board' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONPATH": "src"},
    )

    assert result.stdout.strip() == "False", (
        "task_source pulled fateforger.agents.tasks.board in at import"
    )
