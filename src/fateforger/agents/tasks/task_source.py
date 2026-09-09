"""One port for the day's candidate tickets, and the Notion board behind it.

The planning turn needs the day's candidates twice: the judgement that decides
which of them the person just named reads them, and the card that shows the
person what was on offer reads them. Two reads could disagree -- a row shown
that the judgement never saw, or the reverse -- and nothing would notice. So
the read happens once, through this port, and both halves are handed the same
listing (#401).

The port also gives a second backend somewhere to land. `source` admits
`"ticktick"`; nothing implements it here, and a memory-backed adapter carrying
a next action is work-family increment 2.

**Nothing in this module judges what anyone meant.** Which rows come back is
Notion's own structured filter, decided by the scope; which GTD state a row
lands in is equality against Notion's own enum values -- `Done`, `Blocked`,
`Ready` -- vocabulary the board minted, which is the identifier case CLAUDE.md
holds outside the no-matching rule. Overdue is arithmetic on two dates. A
ticket's prose is carried through as a label and read by nobody here.

**Every failure is loud and carries its cause.** An empty listing means the
board was read and had nothing to offer; a board that could not be read raises
`TaskSourceUnavailable`. The caller draws a different sentence for each, and
returning `[]` for the second would erase the distinction the host's
`work_refs_unresolved` flag exists to preserve.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Protocol

from pydantic import BaseModel

from fateforger.agents.tasks.board import Scope, TaskBoard, TaskListing, TaskRow

Source = Literal["notion", "ticktick"]
CandidateState = Literal["next", "waiting_for", "someday", "done"]

#: One page of a board, sized to be read rather than scrolled. A caller with
#: its own budget passes it -- the planning host asks for `WORK_ROW_LIMIT` --
#: and gets whatever the board holds; the current sprint held twelve Ready
#: rows on 2026-09-08, when the work-lookup judgement was measured
#: (`tests/integration/test_eval_work_lookup.py`).
DEFAULT_CANDIDATE_LIMIT = 12

#: The measured scope: the current sprint's `Ready` rows. Widening it to
#: `Refined` moves what the judgement sees and needs its own eval run, so it is
#: a parameter here and a follow-up on #401, not a default changed in passing.
DEFAULT_SCOPE: Scope = "current_sprint_ready"

# Notion's own enum values on the Tasks database. These are vocabulary the
# board minted -- the same standing as a SQL column name -- and comparing
# against them decides nothing about what a person said.
CLOSED_STATUSES = frozenset({"Done", "Archived"})
BLOCKED_TICKET_STATUS = "Blocked"
SHELVED_TICKET_STATUSES = frozenset({"Paused", "Zombie"})
ACTIONABLE_TICKET_STATUSES = frozenset({"Ready", "Refined"})


class TaskSourceUnavailable(RuntimeError):
    """The board could not be read. Carries the cause."""


class TaskCandidate(BaseModel):
    """One ticket the day could be planned around, as the planner sees it.

    No field carries a default. `overdue` is derived from `due` and the day,
    and a derived field that defaults to `False` is a wrong answer waiting for
    the first row somebody builds by hand; the rest are the board's own facts,
    and an absent one should refuse rather than read as empty.
    """

    source: Source
    #: The backend's own id. Never minted here: a made-up handle is worse than
    #: an absent one, because everything downstream believes it.
    external_id: str
    #: The integer the person says out loud ("do 500 first"), when there is one.
    number: int | None
    label: str
    state: CandidateState
    due: date | None
    overdue: bool
    #: External ids, so the edges stay over identifiers rather than over text.
    blocked_by: list[str]


class TaskCandidates(BaseModel):
    """What one board read offered for one day."""

    day: date
    #: The sprint's name, for the section's head; `None` when the scope
    #: resolved no sprint.
    sprint: str | None
    rows: list[TaskCandidate]
    #: True when the board still had rows it did not return, so a surface can
    #: say this is a page of the board rather than the whole of it.
    truncated: bool


class TaskSource(Protocol):
    """Where a planning turn gets the day's candidate tickets."""

    async def candidates(
        self, day: date, *, limit: int = DEFAULT_CANDIDATE_LIMIT
    ) -> TaskCandidates:
        """The candidates for `day`, or a raised `TaskSourceUnavailable`."""
        ...


def _state_of(row: TaskRow) -> CandidateState:
    """The GTD bucket a board row belongs in, per the work-family table.

    Status is asked first and that ordering is load-bearing: a finished ticket
    keeps whatever Ticket Status it was last left at, so consulting Ticket
    Status first would report a closed ticket as `waiting_for` and offer the
    planner a row nobody can act on.

    An `Unrefined` row -- and any Ticket Status this table does not name --
    falls to `someday` rather than to `next`: it has no next action yet, so it
    is a project, and a project cannot go in a block. The measured scope keeps
    those off the listing anyway; this is what happens when a wider scope is
    passed.
    """
    if row.status in CLOSED_STATUSES:
        return "done"
    if row.ticket_status == BLOCKED_TICKET_STATUS:
        return "waiting_for"
    if row.ticket_status in SHELVED_TICKET_STATUSES:
        return "someday"
    if row.ticket_status in ACTIONABLE_TICKET_STATUSES:
        return "next"
    return "someday"


def _due_date(value: str | None) -> date | None:
    """Notion's Due as a date.

    A Notion date property carries either a plain date or a timestamp, and both
    arrive on the same field, so the timestamp is parsed and its date kept. The
    string is ISO 8601 that Notion wrote; a value that will not parse raises,
    and the caller turns that into one named failure rather than a silently
    absent deadline.
    """
    if value is None:
        return None
    return datetime.fromisoformat(value).date()


def _candidate(row: TaskRow, day: date, source: Source) -> TaskCandidate:
    due = _due_date(row.due)
    return TaskCandidate(
        source=source,
        external_id=row.page_id,
        number=row.number,
        label=row.name,
        state=_state_of(row),
        due=due,
        overdue=due is not None and due < day,
        blocked_by=list(row.blocked_by),
    )


def candidates_from_listing(
    listing: TaskListing, day: date, *, source: Source = "notion"
) -> TaskCandidates:
    """A board listing as candidates, in the order the board returned them.

    The order is the person's own board ranking -- `TaskBoard.list_tasks` sorts
    by Priority descending, and the work-lookup prompt tells the model so.
    Re-sorting here would tell the model a falsehood, and the failure would be
    silent: a well-formed answer naming a real row that is the wrong ticket.
    """
    return TaskCandidates(
        day=day,
        sprint=listing.sprint.name if listing.sprint is not None else None,
        rows=[_candidate(row, day, source) for row in listing.tasks],
        # The board hands back a cursor only when Notion said there is more.
        truncated=listing.next_cursor is not None,
    )


class BoardTaskSource:
    """The `TaskSource` over Hugo's Notion board."""

    def __init__(self, board: TaskBoard, *, scope: Scope = DEFAULT_SCOPE) -> None:
        self._board = board
        self._scope = scope

    async def candidates(
        self, day: date, *, limit: int = DEFAULT_CANDIDATE_LIMIT
    ) -> TaskCandidates:
        """The scope's tickets for `day`, or one named failure carrying its cause.

        Caught broadly, because the failures that matter are not the typed
        ones: `TaskBoard` raises `TaskBoardError` for an error envelope or a
        malformed page, but a Notion outage arrives as an httpx or anyio error
        from inside the MCP client, and that is the likeliest way this fails.
        Every one of them becomes `TaskSourceUnavailable` with the original as
        `__cause__`, so the traceback still names what actually broke.
        """
        try:
            listing = await self._board.list_tasks(self._scope, limit=limit)
            return candidates_from_listing(listing, day)
        except Exception as exc:
            raise TaskSourceUnavailable(
                f"the task board could not be read for {day.isoformat()} "
                f"(scope {self._scope!r}): {exc}"
            ) from exc


__all__ = [
    "ACTIONABLE_TICKET_STATUSES",
    "BLOCKED_TICKET_STATUS",
    "CLOSED_STATUSES",
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_SCOPE",
    "SHELVED_TICKET_STATUSES",
    "BoardTaskSource",
    "CandidateState",
    "Source",
    "TaskCandidate",
    "TaskCandidates",
    "TaskSource",
    "TaskSourceUnavailable",
    "candidates_from_listing",
]
