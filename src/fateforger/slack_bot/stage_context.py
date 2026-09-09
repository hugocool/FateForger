"""The context a Stage 1 session is planning against, as two typed surfaces.

A `ContextPanel` is two blocks: counts by anchor group and one control. A
`ContextFold` is the modal behind that control: every active rule, once,
with a steer menu. Both are built from the snapshot and nothing else -- no
model, no store -- and both order rows the same way, through `rank_rows`,
so the panel's summary and the fold never disagree.

Every comparison here is over identifiers this system minted: constraint
uids, anchor uids, fact ids, enum values, and -- for the board section -- the
integer number and the row id the board itself minted. Anchor names, ticket
names and sprint names are displayed and never compared (CLAUDE.md).
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fateforger.agents.tasks.task_source import CandidateState
from fateforger.agents.timeboxing.elicitation import ALL_CELLS, coverage_matrix, day_label
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningSessionSnapshot,
    suspension_fact_id,
)
from fateforger.agents.timeboxing.work_refs import work_refs_on
from fateforger.slack_bot.messages import SLACK_MAX_MODAL_BLOCKS

Necessity = Literal["must", "should"]
Applies = Literal["every_day", "some_days", "dated"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RankedRow(_Frozen):
    uid: str
    name: str
    necessity: Necessity
    #: (anchor uid, anchor name), as the memory server returned them.
    anchors: list[tuple[str, str]] = Field(default_factory=list)
    fade: float | None = None
    applies: Applies | None = None
    #: The session suspension's reason, when a SUSPENDED_CONSTRAINT fact names this rule.
    suspended_reason: str | None = None
    #: Ordering key 1: suspended this session, or absent from the first draw.
    touched: bool = False
    #: Ordering key 2: an anchor of this rule is placed under a row with an uncovered cell.
    open_concern: bool = False


class AnchorGroup(_Frozen):
    #: None is the unanchored group.
    name: str | None
    uids: list[str]
    must_count: int


def _suspensions(snapshot: PlanningSessionSnapshot) -> dict[str, str]:
    """Constraint uid -> reason, from the session's SUSPENDED_CONSTRAINT facts."""

    found: dict[str, str] = {}
    for fact in snapshot.facts:
        if fact.kind is not FactKind.SUSPENDED_CONSTRAINT:
            continue
        value = fact.value if isinstance(fact.value, dict) else {}
        uid = value.get("uid")
        if isinstance(uid, str) and fact.fact_id == suspension_fact_id(uid):
            found[uid] = str(value.get("reason") or "not today")
    return found


def _open_rows(snapshot: PlanningSessionSnapshot) -> set[str]:
    """Row keys with at least one uncovered cell. Cell ids are catalog ids."""

    matrix = coverage_matrix(snapshot)
    if matrix is None:
        return set()
    return {
        cell.row
        for cell in ALL_CELLS
        if matrix.cells.get(cell.id) == "uncovered"
    }


def _placement(snapshot: PlanningSessionSnapshot) -> dict[str, str]:
    matrix = coverage_matrix(snapshot)
    return dict(matrix.placement) if matrix is not None else {}


def rank_rows(
    snapshot: PlanningSessionSnapshot, first_shown_with: frozenset[str] | None
) -> list[RankedRow]:
    """The snapshot's rows, in the order the panel and the fold show them.

    Four keys, all arithmetic: touched this session; under an open concern;
    nearest to fading (`None` last); then the store's own order, which the
    list already carries, so the sort is stable over it.
    """

    suspended = _suspensions(snapshot)
    open_rows = _open_rows(snapshot)
    placement = _placement(snapshot)
    ranked: list[RankedRow] = []
    for raw in snapshot.applicable_constraints:
        uid = str(raw["uid"])
        anchors = [
            (str(a["uid"]), str(a["name"]))
            for a in (raw.get("anchors") or [])
            if isinstance(a, dict)
        ]
        fade = raw.get("fade")
        ranked.append(
            RankedRow(
                uid=uid,
                name=str(raw["name"]),
                necessity=raw["necessity"],
                anchors=anchors,
                fade=float(fade) if isinstance(fade, (int, float)) else None,
                applies=raw.get("applies"),
                suspended_reason=suspended.get(uid),
                touched=uid in suspended
                or (first_shown_with is not None and uid not in first_shown_with),
                open_concern=any(placement.get(a_uid) in open_rows for a_uid, _ in anchors),
            )
        )
    return sorted(
        ranked,
        key=lambda r: (
            not r.touched,
            not r.open_concern,
            -(r.fade if r.fade is not None else -1.0),
        ),
    )


def primary_anchor(row: RankedRow, sizes: Counter[str]) -> tuple[str, str] | None:
    """The anchor this rule is listed under: the one with the most rules on
    this day, ties broken by the earlier name. A count over minted links
    (anchor uids), not a judgement -- the name only orders a tie."""

    if not row.anchors:
        return None
    return min(row.anchors, key=lambda a: (-sizes[a[0]], a[1]))


def group_rows(rows: list[RankedRow]) -> list[AnchorGroup]:
    """Every rule in exactly one group; groups in the order of their top row.

    Groups are keyed by anchor *name*, not uid: the memory server mints one
    anchor per name, so a name is a safe key here. If that ever changes, key
    `members` by anchor uid instead and carry the name beside it.
    """

    sizes: Counter[str] = Counter(a_uid for row in rows for a_uid, _ in row.anchors)
    order: list[str | None] = []
    members: dict[str | None, list[RankedRow]] = {}
    for row in rows:
        primary = primary_anchor(row, sizes)
        name = primary[1] if primary else None
        if name not in members:
            members[name] = []
            order.append(name)
        members[name].append(row)
    return [
        AnchorGroup(
            name=name,
            uids=[r.uid for r in members[name]],
            must_count=sum(1 for r in members[name] if r.necessity == "must"),
        )
        for name in order
    ]


class SuspendedRow(_Frozen):
    uid: str
    name: str
    reason: str


class WorkItem(_Frozen):
    """One ticket this day is being planned around, as a person reads it.

    The board number and the ticket's own name, and nothing else. The handle
    the ref also carries is a machine identifier: the planner writes it onto a
    block, and showing it here would ask the reader to check a string that
    tells them nothing about which ticket it is. There is no url on the ref at
    all, by design (`work_refs`).
    """

    #: `TaskRow.number` is optional; a ticket nobody numbered is still a ticket.
    number: int | None
    label: str


class BoardCandidateItem(_Frozen):
    """One row the day's board offered, as a person reads it.

    The board's own facts, and one derived flag. No `external_id` and no url:
    a page id says nothing to somebody deciding whether the day is about the
    right ticket, for the same reason `WorkItem` drops the material handle.
    """

    #: `TaskCandidate.number` is optional; a ticket nobody numbered is still
    #: on the board.
    number: int | None
    label: str
    state: CandidateState
    due: date | None
    #: Derived by the port from `due` and the day, never re-derived here.
    overdue: bool
    #: True when one of the day's resolved refs points at this row. The join
    #: is on the board number -- see `_chosen_numbers`. No default, the way
    #: `TaskCandidate.overdue` has none: a derived flag that defaults to False
    #: is a wrong answer waiting for the first item somebody builds by hand.
    chosen: bool


class ContextPanel(_Frozen):
    session_key: str
    expected_revision: int
    #: ISO date of the locked day; a different day means a different panel.
    day: str
    day_label: str
    rule_count: int
    must_count: int
    #: Rules memory holds that do not apply on this kind of day.
    off_today_count: int
    #: The day type, an enum value.
    off_today_reason: str
    groups: list[AnchorGroup]
    #: The tickets the host resolved for this day, in the order it resolved
    #: them. Empty whenever `work_refs_unresolved` is set -- see `_work`.
    work: list[WorkItem] = Field(default_factory=list)
    #: The last resolve that looked could not work out which ticket was meant.
    work_refs_unresolved: bool = False
    #: What the board offered on the last resolve that read one, in the order
    #: the board returned them -- capped by the renderer, not here, so the
    #: count the panel names is a count of what was on offer. Empty whenever
    #: no board was read; `board_read` is what tells that from a board that
    #: was read and offered nothing.
    board: list[BoardCandidateItem] = Field(default_factory=list)
    #: The sprint's name, for the section's head. `None` when the scope
    #: resolved no sprint *and* when the sprint page carries no title, which
    #: are the same fact for a reader: there is no name to print.
    board_sprint: str | None = None
    #: Whether the last resolve read a board at all. Not derivable from
    #: `board`: an empty listing is an answer ("your sprint has nothing"), and
    #: no listing is the absence of one, and the panel says something
    #: different for each.
    board_read: bool = False
    suspended: list[SuspendedRow]
    #: Row uids and suspension fact ids the panel was drawn from. A snapshot
    #: whose set differs needs the panel edited; equal means nothing to do.
    shown_with: frozenset[str]
    #: Row uids at stage entry. Ordering key 1 reads it; kept by the registry.
    first_shown_with: frozenset[str]


#: The mark that stands for "this day's work could not be resolved" inside
#: `shown_with_of`. Minted here, and shaped so it cannot collide with a
#: constraint uid, a suspension fact id or a material handle.
_WORK_UNRESOLVED_MARK = "work-refs:unresolved"

#: The mark that stands for "a board was read this turn". Without it a board
#: that answered with nothing would compare equal to no board at all, and the
#: turn that first read an empty sprint would never redraw the panel to say so.
_BOARD_READ_MARK = "board:read"


def shown_with_of(snapshot: PlanningSessionSnapshot) -> frozenset[str]:
    uids = {str(raw["uid"]) for raw in snapshot.applicable_constraints}
    facts = {
        fact.fact_id
        for fact in snapshot.facts
        if fact.kind is FactKind.SUSPENDED_CONSTRAINT
    }
    # The panel names the day's work too, so the set it is compared by has to
    # move when the work does: the work is resolved on a candidate turn, which
    # usually leaves the rules exactly as they were, and a panel compared on
    # rules alone would never be edited to show the line. By handle, because
    # that is what the host minted per ticket -- a ticket renamed on the board
    # keeps its handle and does not redraw the panel, which is one turn of a
    # stale label and never a wrong ticket.
    work = {
        str(ref["link"])
        for ref in work_refs_on(snapshot.facts)
        if isinstance(ref.get("link"), str)
    }
    marks = {_WORK_UNRESOLVED_MARK} if snapshot.work_refs_unresolved else set()
    # And the board, for the same reason: the section is drawn from the day's
    # candidates, so a turn that changed what the board offered and left the
    # rules alone has to move this set or the section is written and never
    # seen. By external id -- the board's own handle for the row -- following
    # the work above: a ticket renamed or re-dated on the board keeps its id
    # and does not redraw the panel, which is one turn of a stale label and
    # never a row that is not there.
    #
    # **This is a set, so a re-read of the same rows in a new order does not
    # redraw either -- and `timeboxing_cards.BOARD_ROW_CAP` is what makes that
    # safe.** A Priority edit on Notion reorders the listing without changing
    # what is in it; under a cap shorter than the sprint that silently dropped
    # a row, because the panel kept the old top-N while a row that had moved
    # into it was never drawn. At a cap of twelve -- the sprint's real size and
    # the port's own default limit -- every row is on the card whatever the
    # order, so a reorder can no longer lose one, and the cost of the frozenset
    # falls back to what the paragraph above accepts: a stale order for a turn.
    # Lower that cap below the sprint size and this becomes a bug again.
    board: set[str] = set()
    if snapshot.candidates is not None:
        board = {_BOARD_READ_MARK} | {
            f"board:{row.external_id}" for row in snapshot.candidates.rows
        }
    return frozenset(uids | facts | work | marks | board)


def _row_uids(snapshot: PlanningSessionSnapshot) -> frozenset[str]:
    return frozenset(str(raw["uid"]) for raw in snapshot.applicable_constraints)


def _work(snapshot: PlanningSessionSnapshot) -> list[WorkItem]:
    """The tickets the panel may name, which is none of them when the last
    resolve could not answer.

    **A ref can outlive the turn that filed it.** Facts merge by id and are
    never deleted, so a turn that files none leaves the previous turn's ref
    standing on the snapshot; the id is per-day, so the staleness is bounded to
    one day, but one day is the whole session. Naming a ticket beside the
    sentence saying the work could not be worked out is the one combination
    that could get a day approved against the wrong ticket -- the reader has no
    way to tell which half of the line describes this turn.

    A failed lookup now clears the day's refs at the source, by filing
    `WORK_REFS` with an empty value, so in the ordinary flow there is nothing
    left here to suppress. This stays as the second line of defence: a
    snapshot written before that fix, or any later path that sets the flag
    without filing the fact, must still not name a ticket beside a sentence
    disowning it. The sentence itself offers the way back -- say which ticket
    -- and never a name.
    """

    if snapshot.work_refs_unresolved:
        return []
    return [
        WorkItem(
            number=ref["task"] if isinstance(ref.get("task"), int) else None,
            label=ref["label"],
        )
        for ref in work_refs_on(snapshot.facts)
        if isinstance(ref.get("label"), str) and ref["label"]
    ]


def _chosen_numbers(snapshot: PlanningSessionSnapshot) -> frozenset[int]:
    """The board numbers this day's work was taken from.

    **The join is on `task`, and on nothing else.** A ref carries a material
    handle, a label and the board number; the handle is minted per ticket by
    the host and has no counterpart on a candidate, and the label is the
    ticket's prose, which two rows can share and which no comparison here is
    allowed to decide anything by (CLAUDE.md). The number is an integer the
    board minted, so `427 == 427` decides nothing about what anyone meant.

    A ref with no number joins nothing and marks nothing. That understates --
    a ticket nobody numbered can still be the one that was taken -- and
    understating is the right way to be wrong here: an unmarked row reads as
    "not taken from the board", while a wrongly marked one tells the reader
    the day is about a ticket it is not.

    Empty on an unresolved turn, for the reason `_work` returns nothing there:
    the ref standing on the snapshot may be an earlier turn's, and a mark
    beside the sentence saying this turn resolved nothing is the combination
    that could get a day approved against the wrong ticket.
    """

    if snapshot.work_refs_unresolved:
        return frozenset()
    return frozenset(
        ref["task"]
        for ref in work_refs_on(snapshot.facts)
        if isinstance(ref.get("task"), int)
    )


def _board(snapshot: PlanningSessionSnapshot) -> list[BoardCandidateItem]:
    """What the board offered, in the order it offered it.

    No board read is an empty list -- and `board_read` beside it is what keeps
    that from reading as a board that offered nothing.
    """

    if snapshot.candidates is None:
        return []
    chosen = _chosen_numbers(snapshot)
    return [
        BoardCandidateItem(
            number=row.number,
            label=row.label,
            state=row.state,
            due=row.due,
            overdue=row.overdue,
            chosen=row.number is not None and row.number in chosen,
        )
        for row in snapshot.candidates.rows
    ]


def _board_sprint(snapshot: PlanningSessionSnapshot) -> str | None:
    """The sprint's name, or `None` when there is not one to print.

    A sprint page with no title gives back an empty string, and one titled with
    a space gives back a space -- which printed a head of `From your board —  :`
    and is the same fact for a reader: there is no name here. Emptiness, not
    meaning: nothing here compares the title to anything, and the whitespace is
    tested for rather than stripped off a name that has one.
    """

    if snapshot.candidates is None:
        return None
    sprint = snapshot.candidates.sprint
    if sprint is None or not sprint.strip():
        return None
    return sprint


def context_panel(
    snapshot: PlanningSessionSnapshot, first_shown_with: frozenset[str] | None
) -> ContextPanel:
    if snapshot.planning_day is None:
        raise ValueError("a context panel needs a locked planning day")
    seed = first_shown_with if first_shown_with is not None else _row_uids(snapshot)
    rows = rank_rows(snapshot, seed if first_shown_with is not None else None)
    return ContextPanel(
        session_key=snapshot.session_key,
        expected_revision=snapshot.revision,
        day=snapshot.planning_day.date.isoformat(),
        day_label=day_label(snapshot.planning_day),
        rule_count=len(rows),
        must_count=sum(1 for r in rows if r.necessity == "must"),
        off_today_count=snapshot.suspended_constraint_count,
        off_today_reason=snapshot.planning_day.day_type.value,
        groups=group_rows(rows),
        work=_work(snapshot),
        work_refs_unresolved=snapshot.work_refs_unresolved,
        board=_board(snapshot),
        board_sprint=_board_sprint(snapshot),
        board_read=snapshot.candidates is not None,
        suspended=[
            SuspendedRow(uid=r.uid, name=r.name, reason=r.suspended_reason)
            for r in rows
            if r.suspended_reason is not None
        ],
        shown_with=shown_with_of(snapshot),
        first_shown_with=seed,
    )


SteerVerb = Literal["steer_not_today", "steer_wrong", "restore"]


class FoldRow(_Frozen):
    uid: str
    name: str
    necessity: Necessity
    applies: Applies | None
    #: The rule's other anchor names, for the "also" tag.
    also: list[str]
    suspended_reason: str | None
    verbs: list[SteerVerb]


class FoldGroup(_Frozen):
    name: str | None
    rows: list[FoldRow]


class ContextFold(_Frozen):
    session_key: str
    expected_revision: int
    day: str
    day_label: str
    groups: list[FoldGroup]
    off_today_count: int
    off_today_reason: str
    #: (rules, groups) dropped so the view fits the modal cap; None when it fit.
    truncated: tuple[int, int] | None = None


#: One heading section per group, one section per row, one footer section
#: for the memory-side suspensions.
_FOLD_FOOTER_BLOCKS = 1


def fold_block_count(fold: ContextFold) -> int:
    return sum(1 + len(g.rows) for g in fold.groups) + _FOLD_FOOTER_BLOCKS + (
        1 if fold.truncated else 0
    )


def _fold_row(row: RankedRow, primary: tuple[str, str] | None) -> FoldRow:
    verbs: list[SteerVerb] = (
        ["restore"] if row.suspended_reason is not None else ["steer_not_today", "steer_wrong"]
    )
    return FoldRow(
        uid=row.uid,
        name=row.name,
        necessity=row.necessity,
        applies=row.applies,
        also=[name for uid, name in row.anchors if primary is None or uid != primary[0]],
        suspended_reason=row.suspended_reason,
        verbs=verbs,
    )


def _fit_groups_to_cap(
    groups: list[FoldGroup],
) -> tuple[list[FoldGroup], tuple[int, int] | None]:
    """Keep whole groups, top-ranked first, until the fold fits the modal cap.

    Fits everything when it already does -- reserving a "+N" block
    unconditionally would drop a group that fit exactly, which is the
    off-by-one this guards against. Otherwise keeps the longest whole-group
    prefix that leaves room for the "+N" line. If even the top-ranked group
    alone is bigger than that budget, it is kept anyway with its tail rows
    cut by count (never a sliced row of text), so the fold never exceeds
    the cap no matter how lopsided the input is.
    """

    footer = _FOLD_FOOTER_BLOCKS
    cap = SLACK_MAX_MODAL_BLOCKS
    costs = [1 + len(group.rows) for group in groups]
    if sum(costs) + footer <= cap:
        return groups, None

    budget = cap - footer - 1  # one block reserved for the "+N" line
    prefix_len = 0
    used = 0
    for cost in costs:
        if used + cost > budget:
            break
        used += cost
        prefix_len += 1

    if prefix_len > 0:
        kept = groups[:prefix_len]
        dropped = groups[prefix_len:]
        return kept, (sum(len(g.rows) for g in dropped), len(dropped))

    # The top-ranked group alone exceeds the budget: keep it, partially.
    first = groups[0]
    rows_fit = max(budget - 1, 0)  # one block spent on its own heading
    kept_first = FoldGroup(name=first.name, rows=first.rows[:rows_fit])
    dropped_rows = len(first.rows) - len(kept_first.rows)
    dropped_rows += sum(len(g.rows) for g in groups[1:])
    return [kept_first], (dropped_rows, len(groups) - 1)


def context_fold(
    snapshot: PlanningSessionSnapshot, first_shown_with: frozenset[str] | None
) -> ContextFold:
    if snapshot.planning_day is None:
        raise ValueError("a context fold needs a locked planning day")
    rows = rank_rows(snapshot, first_shown_with)
    sizes: Counter[str] = Counter(a_uid for row in rows for a_uid, _ in row.anchors)
    by_uid = {row.uid: row for row in rows}
    groups: list[FoldGroup] = []
    for group in group_rows(rows):
        groups.append(
            FoldGroup(
                name=group.name,
                rows=[
                    _fold_row(by_uid[uid], primary_anchor(by_uid[uid], sizes))
                    for uid in group.uids
                ],
            )
        )
    kept, truncated = _fit_groups_to_cap(groups)
    return ContextFold(
        session_key=snapshot.session_key,
        expected_revision=snapshot.revision,
        day=snapshot.planning_day.date.isoformat(),
        day_label=day_label(snapshot.planning_day),
        groups=kept,
        off_today_count=snapshot.suspended_constraint_count,
        off_today_reason=snapshot.planning_day.day_type.value,
        truncated=truncated,
    )


__all__ = [
    "AnchorGroup",
    "BoardCandidateItem",
    "ContextFold",
    "ContextPanel",
    "FoldGroup",
    "FoldRow",
    "RankedRow",
    "SteerVerb",
    "SuspendedRow",
    "WorkItem",
    "context_fold",
    "context_panel",
    "fold_block_count",
    "group_rows",
    "primary_anchor",
    "rank_rows",
    "shown_with_of",
]
