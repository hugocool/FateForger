"""Show a validated plan to a person.

tmbx renders a plan as a TOON-style table addressed by handle:

    blocks[9]{H,own,type,summary,ST,ET,mode,dur,slug}:
    DWC1,tmbx,DW,Serious C2F work,09:30,11:00,fs,PT1H30M,

That is the right shape for the model -- it patches by handle -- and it was
posted verbatim as the approval card because it was the only form of the
schedule that crossed the server boundary. `plan_apply` now returns the same
resolved rows as data beside the table, and this renders those.

Never from the table. A comma in a summary, a block crossing midnight, a
column renamed on the server -- each is a way a parser here would go quietly
wrong, and the rows already carry every field the table does.

Type codes are shown as codes. tmbx ships no legend for M/C/DW/SW/PR/H/R/BU/BG,
and a label invented here would be a claim about meaning this module cannot
back. The summary carries the meaning; the code is a small tag beside it.
"""

from __future__ import annotations

import html
from datetime import date

#: What tmbx's `own` column calls a block it did not create -- someone else's
#: meeting, an invite. Its own docs say the plan is built around these, so
#: that is the one distinction a reader has to be able to see.
_FOREIGN = "foreign"


def render_schedule(rows: list[dict], *, day: str) -> str:
    """One line per block, in plan order, under the day it belongs to.

    `rows` is `plan_apply`'s `rows` -- dicts with `summary`, `start`, `end`,
    `own`, `type`. Free text is escaped for Slack's reserved characters so a
    summary cannot become formatting or a link.
    """
    heading = f"*{_day_label(day)}*"
    if not rows:
        return f"{heading}\nNo blocks in this plan."

    lines = [heading]
    for row in rows:
        start = _s(row.get("start"))
        end = _s(row.get("end"))
        summary = html.escape(_s(row.get("summary")), quote=False)
        code = _s(row.get("type"))
        tag = f"  `{code}`" if code else ""
        fixed = "  _fixed_" if _s(row.get("own")) == _FOREIGN else ""
        lines.append(f"`{start}–{end}`  {summary}{tag}{fixed}")
    return "\n".join(lines)


def candidate_display_text(candidate: object) -> str:
    """What to show a person for a validated candidate.

    The schedule from its rows when the server sent them; the handle table
    only for an artifact written before it did, because a table is still
    better than nothing. Empty when there is no candidate, so a caller can
    fall through to whatever else it has.
    """
    if candidate is None:
        return ""
    rows = [r for r in (getattr(candidate, "rows", ()) or ()) if isinstance(r, dict)]
    snapshot = getattr(candidate, "snapshot", None)
    day = snapshot.get("day") if isinstance(snapshot, dict) else None
    if rows:
        return render_schedule(rows, day=str(day or ""))
    return (getattr(candidate, "rendered", "") or "").strip()


def _day_label(day: str) -> str:
    """`Wed 26 Aug 2026` when the day parses; the raw string when it does not."""
    try:
        return date.fromisoformat(day[:10]).strftime("%a %-d %b %Y")
    except (ValueError, TypeError):
        return day


def _s(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = ["render_schedule"]
