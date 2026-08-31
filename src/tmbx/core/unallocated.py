"""Measure where a day is under-determined.

``commitment.overspecified`` measures over-determination — every clock pin
that bought nothing. Nothing measured the other direction, so a day with
three unclaimed hours in the middle of it was reported exactly like a full
one, and a planner that had merely run out of things to say looked as
confident as one that had reasoned. Measured on 2026-08-29: the planner
produced a fully pinned eight-block day and the user could not tell which
placements were judgements and which were arbitrary.

This module is the dual measurement, and it is arithmetic and nothing else.
Where the gaps are, how long they run, and which blocks they sit between.
Whether three unallocated hours are a problem, an opportunity, or an
ordinary afternoon is a judgement about what the user meant by their day,
so it belongs to a model. There is no threshold here, no notion of "a lot",
and no advice.

``after``/``before`` are the load-bearing part of the payload. A gap an
agent can anchor to is one it can act on; a bare duration is a complaint.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from pydantic import BaseModel, ConfigDict

from .models import ET, Plan, Resolved

_Interval = tuple[datetime, datetime]


class Gap(BaseModel):
    """One stretch of a day that no block occupies.

    ``start``/``end`` are wall-clock ``time``, the same presentation
    ``Resolved`` and ``ViolationBlock`` use — the arithmetic behind them is
    done on datetimes throughout (see ``unallocated``), and only the
    rendering drops the date. ``duration`` therefore stays correct for a gap
    that crosses midnight, where ``end`` alone would read as earlier than
    ``start``.

    ``after``/``before`` name the chain blocks on either side, and are
    ``None`` where there is no block on that side. They never name a ``BG``
    block: a background window is the boundary a leading or trailing gap is
    measured against, not a neighbour it sits beside.
    """

    model_config = ConfigDict(extra="forbid")

    start: time
    end: time
    duration: timedelta
    after: str | None = None
    before: str | None = None


def _union(intervals: list[_Interval]) -> list[_Interval]:
    """Merge intervals into disjoint, ascending, non-empty spans.

    Dropping zero-length inputs is the load-bearing part. A ``fw`` window
    with ``et == st`` is a legal same-day zero-duration block
    (``_resolve_window`` says so explicitly) and it occupies no time, so it
    can neither cover a gap nor declare availability. Kept, one sitting in
    the middle of a free stretch would cut it in two, reporting three
    unclaimed hours as a pair of shorter gaps that each read as less worth
    asking about.

    Touching intervals merge because a union of two touching spans is one
    span. That is *not* what stops back-to-back blocks from reporting an
    instantaneous gap between every adjacent pair — the strict comparisons
    in ``_holes`` do that, and would do it on unmerged input too.
    """
    merged: list[_Interval] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _holes(region: list[_Interval], covered: list[_Interval]) -> list[_Interval]:
    """``region`` minus ``covered``. Both must already be ``_union``ed.

    Every comparison here is strict, and that is what keeps a zero-length
    span out of the result: a piece of ``region`` that ends exactly where it
    begins is not a gap, and emitting one would put a ``PT0S`` entry between
    every pair of adjacent blocks in the day.
    """
    holes: list[_Interval] = []
    for region_start, region_end in region:
        cursor = region_start
        for covered_start, covered_end in covered:
            if covered_end <= cursor:
                continue
            if covered_start >= region_end:
                break
            if covered_start > cursor:
                holes.append((cursor, covered_start))
            cursor = covered_end
            if cursor >= region_end:
                break
        if cursor < region_end:
            holes.append((cursor, region_end))
    return holes


def unallocated(plan: Plan) -> list[Gap]:
    """Stretches of the day no block occupies, in chronological order.

    The day is measured in two pieces, because the two need different
    warrants:

    * **Interior** — the span from the first chain block's start to the last
      one's end. Reported unconditionally: the space between two placed
      blocks needs no boundary policy to be real.
    * **Leading and trailing** — reported only where a ``BG`` block declares
      the time available. Without one, the day has no stated start or end,
      and measuring midnight to midnight would report the user's sleep as
      unallocated time. ``BG`` is already this codebase's boundary concept:
      it takes no chain time and is excluded from the overlap check.

    Both pieces are then a single set difference — the union of those
    regions minus the union of what the chain occupies — which is what makes
    two awkward cases fall out rather than need rules of their own.
    Overlapping ``BG`` windows are one region, so a gap inside two of them
    is reported once and not twice. Overlapping *blocks* cover their union,
    so a long block spanning two short ones can never leave a phantom gap
    between them: a plan with violations still resolves (``check_overlap`` is
    off here, as it is in ``overspecified``), and reporting free time that a
    block is sitting in would be a worse answer than reporting none.

    Every comparison is on ``start_dt``/``end_dt``, never wall-clock
    ``start``/``end``. A ``FixedWindow`` or a chain that has crossed midnight
    can put two blocks 24h apart with identical times of day; subtracting
    bare ``time`` values would report a negative gap, or a 23-hour one, for a
    day that is simply continuous. ``commitment.overspecified`` documents the
    same trap from the equality side.

    Zero-length gaps are never reported — back-to-back blocks are not a gap
    — and negative ones cannot arise: ``resolve`` refuses a plan containing a
    negative duration, and every span here is a maximal piece of a set
    difference.

    A plan that does not resolve at all returns ``[]``. There are no times to
    subtract, and the caller already has a ``Violation`` describing why.
    """
    try:
        rows = plan.resolve(check_overlap=False)
    except ValueError:
        return []

    # The same ordering resolve() itself uses for the overlap check. Plan
    # order is not chronological — patch ops are set-semantic, so a 19:00
    # block can precede an 09:00 one in the list — and gap arithmetic over
    # list order would measure the wrong pairs.
    chain: list[Resolved] = sorted(
        (row for row in rows if row.t is not ET.BG),
        key=lambda row: (row.start_dt, row.end_dt, row.h),
    )

    background = [(row.start_dt, row.end_dt) for row in rows if row.t is ET.BG]
    interior = [(chain[0].start_dt, max(row.end_dt for row in chain))] if chain else []

    covered = _union([(row.start_dt, row.end_dt) for row in chain])
    region = _union(background + interior)

    # Which block to name when several end (or start) on the same instant.
    # Deterministic under the ordering above: ``after`` is the latest-sorting
    # block ending there — the one a reader would call "the block just
    # before" — and ``before`` the earliest-sorting block starting there.
    ends_at: dict[datetime, str] = {row.end_dt: row.h for row in chain}
    starts_at: dict[datetime, str] = {}
    for row in chain:
        starts_at.setdefault(row.start_dt, row.h)

    return [
        Gap(
            start=start.time(),
            end=end.time(),
            duration=end - start,
            after=ends_at.get(start),
            before=starts_at.get(end),
        )
        for start, end in _holes(region, covered)
    ]


__all__ = ["Gap", "unallocated"]
