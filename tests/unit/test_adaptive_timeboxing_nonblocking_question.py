"""A question can ride with an artifact instead of replacing it.

Today a planner question becomes `AwaitingUser`, which *replaces* the
artifact card -- a question OR a skeleton, never both. That is wrong for a
question the user cannot answer without the day on screen (e.g. "you don't
know when the party ends, and you want 8 hours' sleep"). So a non-blocking
question is presented below the artifact, and Proceed stays live meaning
"approve, question unanswered" (#259).

`skeleton.ordinary_placement` -- the id the brief for this task illustrated
with -- is planner-owned in the readiness catalog (`readiness.py`); a user
blocker naming it is refused as `illegal_user_blocker` before the kernel ever
reaches the blocking/non-blocking branch (see
`test_illegal_user_blocker_names_itself.py`, which exists precisely to keep
that refusal in place). `skeleton.activity_reading` is the one open,
soft, user-owned requirement `_incident_snapshot()` leaves unsatisfied, and
is the id the rest of the suite already uses for a legitimate user blocker
(see `test_a_planner_that_cannot_read_a_name_may_ask_with_its_readings_as_options`
in `test_adaptive_timeboxing.py`), so the tests below use it instead.
"""

from __future__ import annotations

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    AwaitingApproval,
    AwaitingUser,
    PlanningResult,
    TurnFailed,
    UserBlockerDraft,
)
from tests.unit._kernel_fixtures import _ROWS, RowsContextPort, _skeleton_citing
from tests.unit.test_adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
    RecordedPlanner,
    RecordingProgressSink,
    _advance_request,
    _incident_snapshot,
    _kernel,
)


def _blocker(*, blocking: bool, requirement_id: str = "skeleton.activity_reading"):
    return UserBlockerDraft(
        requirement_id=requirement_id,
        why_needed="the party's end is unknown and 8h sleep is wanted",
        blocking=blocking,
    )


def _skeleton_with(blockers: list[UserBlockerDraft]) -> PlanningResult:
    result = _skeleton_citing("a1")
    return result.model_copy(update={"blockers": blockers})


@pytest.mark.asyncio
async def test_a_non_blocking_question_arrives_with_the_artifact() -> None:
    """A placement question is unanswerable without the placement on screen."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_with([_blocker(blocking=False)])),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.question is not None
    assert outcome.question.requirement_id == "skeleton.activity_reading"


@pytest.mark.asyncio
async def test_a_blocking_question_still_stops_the_ladder() -> None:
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_with([_blocker(blocking=True)])),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingUser)


@pytest.mark.asyncio
async def test_two_questions_in_one_turn_are_refused() -> None:
    """At most one question per turn; a second waits for the next draft.

    `_incident_snapshot()` leaves exactly one requirement open, soft and
    user-owned (`skeleton.activity_reading`), so both blockers below cite
    it -- the ladder's "too many" check counts blockers, not distinct
    requirements, and this is the only id available to prove that without a
    bespoke snapshot.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo,
        RecordedPlanner(_skeleton_with([
            _blocker(blocking=False),
            _blocker(blocking=False),
        ])),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "too_many_questions"


@pytest.mark.asyncio
async def test_a_non_blocking_question_with_no_artifact_still_blocks() -> None:
    """A question with nothing to attach to is a question that must block,
    or it would vanish. `_skeleton_citing` always includes the skeleton
    artifact, so drop it here to land in the no-artifact branch."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    result = PlanningResult(
        artifact_updates=[],
        blockers=[_blocker(blocking=False)],
    )
    outcome = await _kernel(
        repo, RecordedPlanner(result),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingUser)
    assert outcome.requirement_id == "skeleton.activity_reading"
