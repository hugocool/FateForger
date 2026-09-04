"""The context a Stage 1 session is planning against, as two typed surfaces.

A `ContextPanel` is two blocks: counts by anchor group and one control. A
`ContextFold` is the modal behind that control: every active rule, once,
with a steer menu. Both are built from the snapshot and nothing else -- no
model, no store -- and both order rows the same way, through `rank_rows`,
so the panel's summary and the fold never disagree.

Every comparison here is over identifiers this system minted: constraint
uids, anchor uids, fact ids, enum values. Anchor names are displayed and
never compared (CLAUDE.md).
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fateforger.agents.timeboxing.elicitation import ALL_CELLS, coverage_matrix, day_label
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningSessionSnapshot,
    suspension_fact_id,
)

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
    suspended: list[SuspendedRow]
    #: Row uids and suspension fact ids the panel was drawn from. A snapshot
    #: whose set differs needs the panel edited; equal means nothing to do.
    shown_with: frozenset[str]
    #: Row uids at stage entry. Ordering key 1 reads it; kept by the registry.
    first_shown_with: frozenset[str]


def shown_with_of(snapshot: PlanningSessionSnapshot) -> frozenset[str]:
    uids = {str(raw["uid"]) for raw in snapshot.applicable_constraints}
    facts = {
        fact.fact_id
        for fact in snapshot.facts
        if fact.kind is FactKind.SUSPENDED_CONSTRAINT
    }
    return frozenset(uids | facts)


def _row_uids(snapshot: PlanningSessionSnapshot) -> frozenset[str]:
    return frozenset(str(raw["uid"]) for raw in snapshot.applicable_constraints)


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
        suspended=[
            SuspendedRow(uid=r.uid, name=r.name, reason=r.suspended_reason)
            for r in rows
            if r.suspended_reason is not None
        ],
        shown_with=shown_with_of(snapshot),
        first_shown_with=seed,
    )


__all__ = [
    "AnchorGroup",
    "ContextPanel",
    "RankedRow",
    "SuspendedRow",
    "context_panel",
    "group_rows",
    "primary_anchor",
    "rank_rows",
    "shown_with_of",
]
