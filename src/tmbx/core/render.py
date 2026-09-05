"""Render a plan for the model.

This is what the model reads every turn, and it is the other half of the
addressing design: ops (see ``ops.py``) address blocks by handle, so the
render must show handles — there is nothing else the model could name a
block by.

It deliberately shows resolved times *and* structure together. Resolved-only
would force the model to write fixed times because that is all it can see,
killing least commitment. Structural-only would force it to compute "what is
after 14:00", which is the arithmetic ``Plan.resolve()`` exists to remove
from its job.

Three deltas from the legacy ``timebox_events_rows``
(``fateforger/agents/timeboxing/toon_views.py``) this replaces:

* ``H`` is the first column — it is the addressing key; without it the model
  has nothing to address.
* The boolean anchor flag (``AP: true/false``) becomes the actual mode
  (``ap``/``bn``/``fs``/``fw``). A boolean collapses four modes into two and
  hides exactly what least-commitment depends on.
* Durations are ISO (``PT45M``), not ``total_seconds()`` — fewer tokens and
  far easier to read.

``uid`` is never rendered. The handle stands in for it.

Free-text fields (currently just the name) are quoted when they would
otherwise break the table — see ``_escape``.

The ``own`` column marks blocks tmbx does not own — real calendar events
such as someone else's meeting, present in the plan as read-only context
the chain must respect but that no op may ever remove/update/move (see
``PlanService._plan_from_calendar``/``_foreign_touches``). Without this
column a model has no way to tell an editable block from an immovable one
except by triggering a refusal first; an ``EVT``-prefixed handle is only a
coincidental partial signal (any block with no calendar-provided handle
gets one, tmbx-owned or not) and must not be pattern-matched on instead.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import date as date_type
from datetime import datetime, time, timedelta

from isodate import duration_isoformat

from .models import Plan, Resolved

COLUMNS = ("H", "own", "type", "summary", "ST", "ET", "mode", "dur", "slug")

_DELIMITER = ","

EMPTY_DAY_LINE = "(empty day: no blocks on this calendar for this date)"
"""What follows the header when the plan has no blocks.

A bare ``blocks[0]{...}:`` -- a column spec, a colon, nothing -- is what
``plan_read`` returned for an empty day on 2026-09-02 (#253), and to the
model reading it, it looks like a string cut off mid-way rather than a
fact about the day. The header stays (its count is the table's contract);
this sentence makes "nothing here" explicit. It starts with ``(`` so it
cannot be mistaken for a row -- rows begin with a handle.
"""


def _escape(value: str) -> str:
    """Quote a free-text field so it can't be mistaken for column structure.

    ``Block.n`` is unconstrained user prose — ``"Sprint, planning"`` is an
    entirely ordinary name, and rendered raw it would inject an extra
    delimiter, shifting every column after it. A literal newline would
    split one row across two physical lines, making the next row look
    malformed. Neither is a judgement about what the text *means* — it is
    quoting a syntactic boundary, the same thing CSV quoting does, so it
    does not fall under the "no judging user meaning by string means" ban.

    Mirrors ``fateforger.llm.toon._toon_escape``'s rule exactly (quote on
    delimiter/newline/CR/quote/surrounding whitespace, double an embedded
    quote) without importing it — ``src/tmbx`` must never import
    ``fateforger``.
    """
    if value == "":
        return value
    needs_quote = (
        _DELIMITER in value
        or "\n" in value
        or "\r" in value
        or '"' in value
        or value != value.strip()
    )
    if not needs_quote:
        return value
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _iso_duration(value: timedelta) -> str:
    """ISO 8601 duration, with one deliberate override: a zero-length
    window (``FixedWindow`` permits ``et == st`` — see ``models.py``)
    renders as ``PT0S``, not ``isodate``'s own ``P0D``. Both are valid ISO
    8601 for a zero duration, but ``PT0S`` reads unambiguously as "a
    time span of zero" where ``P0D`` reads as "zero days" -- easy to
    misparse as a whole-day block at a glance.
    """
    if value == timedelta(0):
        return "PT0S"
    return duration_isoformat(value)


def _fmt_clock(t: time, dt: datetime, plan_date: date_type) -> str:
    """Wall-clock ``HH:MM``, with a day-offset suffix when ``dt``'s calendar
    date differs from the plan's own date.

    A ``FixedWindow`` may legitimately cross midnight (``et < st``), and a
    chain built from ``ap``/``bn`` durations can drift across midnight too.
    ``Resolved.start``/``end`` alone are bare wall-clock times, so a block
    that runs 23:00 -> 01:00 would render as an apparently negative span
    with nothing to say it isn't a data error. The offset suffix (``+1``,
    ``-1``, ...) makes the day change explicit instead of silent.
    """
    offset = (dt.date() - plan_date).days
    base = t.strftime("%H:%M")
    if offset == 0:
        return base
    sign = "+" if offset > 0 else ""
    return f"{base}{sign}{offset}"


def _row(plan_date: date_type, r: Resolved, *, foreign: bool) -> str:
    return _DELIMITER.join(
        [
            r.h,
            "foreign" if foreign else "tmbx",
            r.t.value,
            _escape(r.n),
            _fmt_clock(r.start, r.start_dt, plan_date),
            _fmt_clock(r.end, r.end_dt, plan_date),
            r.mode,
            _iso_duration(r.dur),
        ]
    )


def plan_rows(plan: Plan, foreign_uids: Collection[str] = ()) -> list[dict[str, str]]:
    """The rows behind `render_plan`, as data.

    Same resolution, same ownership rule, same clock formatting -- one dict
    per block, keyed by the table's own column names. A host that shows the
    schedule to a person renders from these; before this the table was the
    only form that crossed the boundary, so a human view had to parse it back.
    """
    foreign = set(foreign_uids)
    # `Resolved` carries only what resolution computes -- times, mode,
    # duration -- and `slug` is a static field on the block, not something
    # resolution derives. Read it from the blocks by handle instead of
    # assuming the resolved row exposes it.
    slugs_by_h = {b.h: b.slug for b in plan.blocks}
    return [
        {
            "h": r.h,
            "own": "foreign" if r.uid in foreign else "tmbx",
            "type": r.t.value,
            "summary": r.n,
            "start": _fmt_clock(r.start, r.start_dt, plan.date),
            "end": _fmt_clock(r.end, r.end_dt, plan.date),
            "mode": r.mode,
            "dur": _iso_duration(r.dur),
            "slug": slugs_by_h.get(r.h) or "",
        }
        for r in plan.resolve(check_overlap=False)
    ]


def render_plan(plan: Plan, foreign_uids: Collection[str] = ()) -> str:
    """Render ``plan`` as a TOON-style table: a header naming the row count
    and columns, then one comma-separated row per block, in plan order.

    Uses ``plan.resolve(check_overlap=False)`` — rendering is a read, not a
    validation step, and must not raise just because a plan happens to
    overlap; that is a separate check's job.

    ``slug`` is the recurring kind of block (``planning``, ``sleep``), shown
    so a planner can see a required kind is already on the day; empty when
    the block has none.

    ``foreign_uids`` — a block's ``uid`` (never rendered itself, see the
    module docstring) — controls the ``own`` column: ``"foreign"`` when
    ``r.uid`` is in the set, ``"tmbx"`` otherwise. Defaults to empty so a
    caller with nothing foreign to mark (or that doesn't track ownership at
    all) still gets valid output; ``PlanService`` is the only caller that
    knows the real set, from the same calendar fetch that built the plan.
    """
    header = f"blocks[{len(plan.blocks)}]{{{','.join(COLUMNS)}}}:"
    if not plan.blocks:
        return f"{header}\n{EMPTY_DAY_LINE}"

    # Built from the same rows a host renders for a person, so the table the
    # model patches against and the schedule the user approves cannot differ.
    lines = [header] + [
        _DELIMITER.join(
            [row["h"], row["own"], row["type"], _escape(row["summary"]),
             row["start"], row["end"], row["mode"], row["dur"], row["slug"]]
        )
        for row in plan_rows(plan, foreign_uids)
    ]
    return "\n".join(lines)


__all__ = ["COLUMNS", "EMPTY_DAY_LINE", "plan_rows", "render_plan"]
