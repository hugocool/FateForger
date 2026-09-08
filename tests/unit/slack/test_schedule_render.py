"""A validated plan was shown to Hugo as tmbx's handle table.

    blocks[9]{H,own,type,summary,ST,ET,mode,dur}:
    DWC1,tmbx,DW,Serious C2F work,09:30,11:00,fs,PT1H30M

That table is addressed by handle so the model can patch by handle. It is the
right shape for the model and the wrong shape for a person, and it was posted
verbatim as the approval card because it was the only form of the schedule
that crossed the server boundary. This renders the same rows for a reader.

It renders from structured rows, never by parsing the table: a comma inside a
summary, a block crossing midnight, a renamed column -- each is a way a parser
would go quietly wrong, and the rows already exist.
"""

from __future__ import annotations

from fateforger.slack_bot.schedule_render import render_schedule

ROWS = [
    {"h": "DWC1", "own": "tmbx", "type": "DW", "summary": "Serious C2F work",
     "start": "09:30", "end": "11:00", "mode": "fs", "dur": "PT1H30M"},
    {"h": "BRK1", "own": "tmbx", "type": "BU", "summary": "Break",
     "start": "11:00", "end": "11:15", "mode": "ap", "dur": "PT15M"},
    {"h": "EVT1", "own": "foreign", "type": "M", "summary": "Kapper",
     "start": "12:00", "end": "12:30", "mode": "fw", "dur": "PT30M"},
]


def test_each_block_is_a_line_with_its_times_and_summary():
    text = render_schedule(ROWS, day="2026-08-26")
    assert "09:30–11:00" in text
    assert "Serious C2F work" in text
    assert "12:00–12:30" in text and "Kapper" in text


def test_the_handle_table_syntax_never_reaches_the_reader():
    text = render_schedule(ROWS, day="2026-08-26")
    assert "blocks[" not in text
    assert "{H,own,type" not in text
    assert ",tmbx," not in text


def test_a_block_tmbx_does_not_own_is_marked_as_fixed():
    """The one distinction a reader must see: which blocks the plan built
    around rather than built. tmbx's own docs call these foreign -- someone
    else's meeting, an invite -- and they occupy real time."""
    text = render_schedule(ROWS, day="2026-08-26")
    kapper = next(line for line in text.splitlines() if "Kapper" in line)
    assert "fixed" in kapper
    c2f = next(line for line in text.splitlines() if "C2F" in line)
    assert "fixed" not in c2f


def test_blocks_keep_plan_order():
    text = render_schedule(ROWS, day="2026-08-26")
    assert text.index("Serious C2F work") < text.index("Break") < text.index("Kapper")


def test_the_day_is_named_once_at_the_top():
    text = render_schedule(ROWS, day="2026-08-26")
    assert text.splitlines()[0].startswith("*")
    assert "26" in text.splitlines()[0]


def test_no_rows_says_so_rather_than_rendering_nothing():
    """An empty string looks like a rendering failure; a sentence does not."""
    assert render_schedule([], day="2026-08-26").strip()


def test_mrkdwn_specials_in_a_summary_do_not_become_formatting():
    rows = [dict(ROWS[0], summary="Review *draft* & <plan>")]
    text = render_schedule(rows, day="2026-08-26")
    assert "&amp;" in text and "&lt;plan&gt;" in text
