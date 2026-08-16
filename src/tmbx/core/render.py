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
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time

from isodate import duration_isoformat

from .models import Plan, Resolved

COLUMNS = ("H", "type", "summary", "ST", "ET", "mode", "dur")


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


def _row(plan_date: date_type, r: Resolved) -> str:
    return ",".join(
        [
            r.h,
            r.t.value,
            r.n,
            _fmt_clock(r.start, r.start_dt, plan_date),
            _fmt_clock(r.end, r.end_dt, plan_date),
            r.mode,
            duration_isoformat(r.dur),
        ]
    )


def render_plan(plan: Plan) -> str:
    """Render ``plan`` as a TOON-style table: a header naming the row count
    and columns, then one comma-separated row per block, in plan order.

    Uses ``plan.resolve(check_overlap=False)`` — rendering is a read, not a
    validation step, and must not raise just because a plan happens to
    overlap; that is a separate check's job.
    """
    header = f"blocks[{len(plan.blocks)}]{{{','.join(COLUMNS)}}}:"
    if not plan.blocks:
        return header

    rows = plan.resolve(check_overlap=False)
    lines = [header] + [_row(plan.date, r) for r in rows]
    return "\n".join(lines)


__all__ = ["COLUMNS", "render_plan"]
