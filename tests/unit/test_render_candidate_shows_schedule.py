"""The approval card shows a person the schedule, not tmbx's handle table.

`render_candidate` posted `owned.rendered` -- the model-facing table addressed
by handle -- as the card body. #272 put the resolved rows into the candidate
and a human render beside them; this is the card picking them up.
"""

from __future__ import annotations

from types import SimpleNamespace

from fateforger.slack_bot.timebox_candidate import (
    PendingTimeboxCandidates,
    ValidatedTimeboxCandidate,
)
from fateforger.slack_bot.timeboxing_cards import render_candidate

ROWS = [
    {"h": "DWC1", "own": "tmbx", "type": "DW", "summary": "Serious C2F work",
     "start": "09:30", "end": "11:00", "mode": "fs", "dur": "PT1H30M"},
    {"h": "EVT1", "own": "foreign", "type": "M", "summary": "Kapper",
     "start": "12:00", "end": "12:30", "mode": "fw", "dur": "PT30M"},
]
TABLE = (
    "blocks[2]{H,own,type,summary,ST,ET,mode,dur}:\n"
    "DWC1,tmbx,DW,Serious C2F work,09:30,11:00,fs,PT1H30M\n"
    "EVT1,foreign,M,Kapper,12:00,12:30,fw,PT30M"
)


def _card(rows) -> str:
    payload = ValidatedTimeboxCandidate(
        digest="d" * 64,
        snapshot={"calendar_id": "primary", "day": "2026-08-26"},
        patch={"ops": []},
        rendered=TABLE,
        rows=tuple(rows),
    ).as_commit_basis()
    message = render_candidate(
        SimpleNamespace(payload=payload),
        pending=PendingTimeboxCandidates(),
        session_key="C1:171.1",
        actor_user_id="U1",
        expected_revision=3,
    )
    section = next(b for b in message.blocks if b.get("type") == "section")
    return section["text"]["text"]


def test_the_card_body_is_the_schedule_when_rows_are_present():
    body = _card(ROWS)
    assert "09:30–11:00" in body and "Serious C2F work" in body
    assert "blocks[" not in body and ",tmbx," not in body


def test_a_foreign_block_is_marked_fixed_on_the_card():
    body = _card(ROWS)
    kapper = next(line for line in body.splitlines() if "Kapper" in line)
    assert "fixed" in kapper


def test_an_older_artifact_without_rows_still_shows_the_table():
    """Written before the server returned rows; the table beats a blank card."""
    assert _card([]) == TABLE


def test_the_approve_control_survives_the_change():
    payload = ValidatedTimeboxCandidate(
        digest="d" * 64, snapshot={"day": "2026-08-26"}, patch={}, rendered=TABLE, rows=tuple(ROWS)
    ).as_commit_basis()
    message = render_candidate(
        SimpleNamespace(payload=payload),
        pending=PendingTimeboxCandidates(),
        session_key="C1:171.1",
        actor_user_id="U1",
        expected_revision=3,
    )
    assert any(b.get("type") == "actions" for b in message.blocks)
