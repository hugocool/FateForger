"""The approval card shows a person the schedule, not tmbx's handle table.

The stage-4 card used to post `owned.rendered` -- the model-facing table
addressed by handle -- as its body. #272 put the resolved rows into the
candidate and a human render beside them; this is the card picking them up.
The card is built by `map_outcome` (increment A); the assertions are over the
card's body and its controls.
"""

from __future__ import annotations

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    PlanningArtifact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import StageCard, map_outcome
from fateforger.slack_bot.timebox_candidate import (
    PendingTimeboxCandidates,
    ValidatedTimeboxCandidate,
)

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


def _card(rows) -> StageCard:
    payload = ValidatedTimeboxCandidate(
        digest="d" * 64,
        snapshot={"calendar_id": "primary", "day": "2026-08-26"},
        patch={"ops": []},
        rendered=TABLE,
        rows=tuple(rows),
    ).as_commit_basis()
    artifact = PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload=payload,
        dependency_revisions={"skeleton": 1},
    )
    snapshot = PlanningSessionSnapshot(session_key="C1:171.1", revision=3, owner_user_id="U1")
    card = map_outcome(
        AwaitingApproval(artifact=artifact),
        snapshot,
        pending=PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:171.1",
        channel_id="C1",
        thread_ts="171.1",
    )
    assert card is not None
    return card


def test_the_card_body_is_the_schedule_when_rows_are_present():
    body = _card(ROWS).body
    assert "09:30–11:00" in body and "Serious C2F work" in body
    assert "blocks[" not in body and ",tmbx," not in body


def test_a_foreign_block_is_marked_fixed_on_the_card():
    body = _card(ROWS).body
    kapper = next(line for line in body.splitlines() if "Kapper" in line)
    assert "fixed" in kapper


def test_an_older_artifact_without_rows_still_shows_the_table():
    """Written before the server returned rows; the table beats a blank card."""
    assert _card([]).body == TABLE


def test_the_approve_control_survives_the_change():
    assert any(control.kind == "commit" for control in _card(ROWS).controls)
