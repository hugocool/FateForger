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

COLUMNS = ("H", "own", "type", "summary", "ST", "ET", "mode", "dur")

_DELIMITER = ","


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


def render_plan(plan: Plan, foreign_uids: Collection[str] = ()) -> str:
    """Render ``plan`` as a TOON-style table: a header naming the row count
    and columns, then one comma-separated row per block, in plan order.

    Uses ``plan.resolve(check_overlap=False)`` — rendering is a read, not a
    validation step, and must not raise just because a plan happens to
    overlap; that is a separate check's job.

    ``foreign_uids`` — a block's ``uid`` (never rendered itself, see the
    module docstring) — controls the ``own`` column: ``"foreign"`` when
    ``r.uid`` is in the set, ``"tmbx"`` otherwise. Defaults to empty so a
    caller with nothing foreign to mark (or that doesn't track ownership at
    all) still gets valid output; ``PlanService`` is the only caller that
    knows the real set, from the same calendar fetch that built the plan.
    """
    header = f"blocks[{len(plan.blocks)}]{{{','.join(COLUMNS)}}}:"
    if not plan.blocks:
        return header

    foreign = set(foreign_uids)
    rows = plan.resolve(check_overlap=False)
    lines = [header] + [_row(plan.date, r, foreign=r.uid in foreign) for r in rows]
    return "\n".join(lines)


__all__ = ["COLUMNS", "render_plan"]
