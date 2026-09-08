"""What a person sees for a validated candidate: the schedule, not the table.

`harness_bridge` used to hand `candidate.rendered` -- tmbx's handle table --
straight back as the turn's answer. Now the server returns the resolved rows
beside the table and the artifact carries them, so the answer can be a
schedule. The table remains the fallback for an artifact written before the
rows existed, because a table is still better than nothing.
"""

from __future__ import annotations

import json

from fateforger.slack_bot.schedule_render import candidate_display_text
from fateforger.slack_bot.timebox_candidate import ValidatedTimeboxCandidate
from fateforger.slack_bot.validated_timebox_draft import (
    read_validated_candidate,
    record_validation_result,
)

ROWS = [
    {"h": "DWC1", "own": "tmbx", "type": "DW", "summary": "Serious C2F work",
     "start": "09:30", "end": "11:00", "mode": "fs", "dur": "PT1H30M"},
]
TABLE = "blocks[1]{H,own,type,summary,ST,ET,mode,dur}:\nDWC1,tmbx,DW,Serious C2F work,09:30,11:00,fs,PT1H30M"


def _candidate(**kw) -> ValidatedTimeboxCandidate:
    base = dict(digest="d", snapshot={"day": "2026-08-26"}, patch={}, rendered=TABLE)
    base.update(kw)
    return ValidatedTimeboxCandidate(**base)


def test_rows_render_as_a_schedule_and_the_table_is_not_shown():
    text = candidate_display_text(_candidate(rows=tuple(ROWS)))
    assert "09:30–11:00" in text and "Serious C2F work" in text
    assert "blocks[" not in text


def test_an_artifact_without_rows_falls_back_to_the_table():
    """Written before the server returned rows; the table is all there is."""
    assert candidate_display_text(_candidate()) == TABLE


def test_no_candidate_is_empty_so_the_caller_can_fall_through():
    assert candidate_display_text(None) == ""


def test_rows_survive_the_artifact_round_trip(tmp_path):
    """Hook writes the artifact; the bridge reads it back. Rows written but
    not read would leave the answer on the table with nothing to say why."""
    event = {
        "tool_name": "mcp__tmbx__plan_apply",
        "hook_event_name": "PostToolUse",
        "tool_input": {
            "snapshot": {"calendar_id": "c", "day": "2026-08-26", "tz": "Europe/Amsterdam",
                         "etags": {}, "event_ids": []},
            "patch": {"ops": [{"op": "add", "h": "DWC1", "n": "Serious C2F work", "t": "DW",
                               "p": {"a": "fs", "st": "09:30:00", "dur": "PT1H30M"}}]},
        },
        "tool_response": json.dumps(
            {"ok": True, "committable": True, "violations": [], "rendered": TABLE, "rows": ROWS}
        ),
    }
    state, out = tmp_path / "state.json", tmp_path / "candidate.json"
    record_validation_result(event, str(state), str(out))
    candidate = read_validated_candidate(str(out))
    assert candidate is not None
    assert [r["summary"] for r in candidate.rows] == ["Serious C2F work"]
    assert "09:30–11:00" in candidate_display_text(candidate)


def test_a_malformed_row_in_the_artifact_is_dropped_not_guessed(tmp_path):
    payload = _candidate(rows=tuple(ROWS)).as_commit_basis()
    payload["rows"] = [ROWS[0], "not a row", 42]
    rebuilt = ValidatedTimeboxCandidate.from_artifact_payload(payload)
    assert len(rebuilt.rows) == 1
