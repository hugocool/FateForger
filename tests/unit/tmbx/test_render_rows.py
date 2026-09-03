"""The resolved rows behind the rendered table, as data.

`render_plan` is a TOON-style table addressed by handle -- the right shape for
a model that patches by handle, and the wrong shape to show a person. Until
now it was the only form of the schedule that crossed the server boundary, so
a host that wanted a human view had to parse the table back. `plan_rows` is
the same resolution, the same ownership rule, the same clock formatting, as
structured rows -- so the table and any human render cannot disagree.
"""

from __future__ import annotations

from datetime import date

from tmbx.core.models import ET, Block, FixedStart, Plan
from tmbx.core.render import plan_rows, render_plan

DAY = date(2026, 8, 26)


def _block(h: str, n: str, t: ET, st: str, dur: str, uid: str) -> Block:
    return Block(
        uid=uid, h=h, n=n, t=t,
        p=FixedStart(st=st, dur=dur),
        anchor_source="user",
    )


def _plan() -> Plan:
    return Plan(
        date=DAY,
        blocks=[
            _block("DW1", "Serious C2F work", ET.DW, "09:30:00", "PT1H30M", "u-dw"),
            _block("EVT1", "Kapper", ET.M, "12:00:00", "PT30M", "u-evt"),
        ],
    )


def test_rows_carry_the_same_facts_as_the_table():
    rows = plan_rows(_plan(), foreign_uids={"u-evt"})
    assert [r["h"] for r in rows] == ["DW1", "EVT1"]
    assert rows[0] == {
        "h": "DW1", "own": "tmbx", "type": "DW", "summary": "Serious C2F work",
        "start": "09:30", "end": "11:00", "mode": "fs", "dur": "PT1H30M",
    }
    assert rows[1]["own"] == "foreign"
    assert rows[1]["summary"] == "Kapper"


def test_rows_and_table_agree_on_order_and_ownership():
    """One resolution feeds both; drifting apart is the failure this pins."""
    plan = _plan()
    rows = plan_rows(plan, foreign_uids={"u-evt"})
    table_lines = render_plan(plan, foreign_uids={"u-evt"}).splitlines()[1:]
    assert [r["h"] for r in rows] == [line.split(",")[0] for line in table_lines]
    assert [r["own"] for r in rows] == [line.split(",")[1] for line in table_lines]


def test_an_empty_plan_has_no_rows():
    assert plan_rows(Plan(date=DAY, blocks=[])) == []
