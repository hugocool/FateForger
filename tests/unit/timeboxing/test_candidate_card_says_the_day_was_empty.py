"""Building a whole day onto an empty calendar read is a decision the user sees (#251).

On 2026-09-02 the read returned `blocks: 0` and the candidate added 19 blocks.
That was probably right -- the journal agrees the day was empty -- but the
card showed the plan as if it were refining a day, and the one fact that
would have made the user look twice was nowhere on it.
"""

from __future__ import annotations

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    PlanningArtifact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import map_outcome
from fateforger.slack_bot.timeboxing_cards import (
    PendingTimeboxCandidates,
    render_stage_card,
)


def _candidate(*, event_ids: dict[str, str], ops: list[dict]) -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload={
            "digest": "d" * 64,
            "snapshot": {
                "token": "tok",
                "calendar_id": "cal",
                "day": "2026-09-02",
                "tz": "Europe/Amsterdam",
                "etags": {},
                "event_ids": event_ids,
            },
            "patch": {"ops": ops},
            "rendered": "09:30 Finances\n10:00 Deep work",
        },
        dependency_revisions={"skeleton": 1},
    )


def _render(artifact: PlanningArtifact) -> str:
    card = map_outcome(
        AwaitingApproval(artifact=artifact),
        PlanningSessionSnapshot(session_key="C1:1.0", revision=5, owner_user_id="U1"),
        pending=PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )
    assert card is not None
    return render_stage_card(card).text


def test_an_empty_read_built_into_a_full_day_is_said_on_the_card() -> None:
    text = _render(
        _candidate(event_ids={}, ops=[{"op": "add", "uid": f"u{i}"} for i in range(19)])
    )

    assert "empty" in text
    assert "19" in text
    assert "09:30 Finances" in text


def test_a_day_that_already_had_blocks_carries_no_such_notice() -> None:
    text = _render(
        _candidate(
            event_ids={"u1": "ev1"},
            ops=[{"op": "add", "uid": "u2"}, {"op": "move", "uid": "u1"}],
        )
    )

    assert "empty" not in text
    assert "09:30 Finances" in text
