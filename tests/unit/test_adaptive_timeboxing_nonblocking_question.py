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

from fateforger.agents.timeboxing.adaptive_timeboxing import TurnRequest
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ApproveArtifact,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    ChooseBlockerOption,
    FactKind,
    NeedsAnotherTurn,
    PlannerContinuation,
    PlanningResult,
    TurnFailed,
    UserBlockerDraft,
)
from tests.unit._kernel_fixtures import _ROWS, RowsContextPort, _skeleton_citing
from tests.unit.test_adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
    RecordedPlanner,
    RecordingProgressSink,
    _ScriptedPlanner,
    _advance_request,
    _candidate_result,
    _fact,
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


@pytest.mark.asyncio
async def test_a_non_blocking_question_survives_a_continuation() -> None:
    """The question must not vanish just because the planner is not done.

    `NeedsAnotherTurn` renders no card today, so nothing shows this
    question to the user this turn either -- but the outcome must still
    carry it rather than leave its survival up to whether the planner
    happens to raise the same blocker again next turn (#259 fix round 1).
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    result = _skeleton_with([_blocker(blocking=False)]).model_copy(
        update={
            "continuation": PlannerContinuation(reason="still balancing the evening")
        }
    )
    outcome = await _kernel(
        repo, RecordedPlanner(result), context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, NeedsAnotherTurn)
    assert outcome.question is not None
    assert outcome.question.requirement_id == "skeleton.activity_reading"


@pytest.mark.asyncio
async def test_a_riding_questions_option_press_is_accepted_and_recorded() -> None:
    """Finding 2: the riding question's buttons must answer for real, or the
    feature is cosmetic. A press against it must not refuse as
    `stale_blocker_choice` the way an unheld question would."""

    options = [
        BlockerOption(
            option_id="wake-1",
            label="Protect a wake time",
            effect="keeps 08:00 wake even if the party runs late",
        ),
        BlockerOption(
            option_id="wake-2",
            label="Leave the evening open",
            effect="the wake time moves with the party",
        ),
    ]
    riding_result = _skeleton_with(
        [
            UserBlockerDraft(
                requirement_id="skeleton.activity_reading",
                why_needed="the party's end is unknown and 8h sleep is wanted",
                blocking=False,
                options=options,
            )
        ]
    )
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(riding_result, _skeleton_citing("a1"))
    kernel = _kernel(repo, planner, context=RowsContextPort(_ROWS))

    first = await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    assert isinstance(first, AwaitingApproval)
    assert first.question is not None

    outcome = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.press",
            actor_user_id="U1",
            expected_revision=4,
            intent=ChooseBlockerOption(
                requirement_id="skeleton.activity_reading", option_id="wake-1"
            ),
        ),
        progress=RecordingProgressSink(),
    )

    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    recorded = [f for f in saved.facts if f.kind is FactKind.ACTIVITY_READING]

    assert not isinstance(outcome, TurnFailed), outcome
    assert isinstance(outcome, AwaitingApproval)
    assert len(recorded) == 1
    assert recorded[0].value == {
        "requirement_id": "skeleton.activity_reading",
        "label": "Protect a wake time",
        "effect": "keeps 08:00 wake even if the party runs late",
    }
    assert saved.pending_blocker is None


@pytest.mark.asyncio
async def test_proceeding_with_the_question_unanswered_still_produces_the_plan() -> None:
    """Proceed means 'approve, question unanswered' -- pressing it must not
    get stuck on the `pending_blocker` the riding question now holds."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(
        _skeleton_with([_blocker(blocking=False)]),
        _candidate_result(),
    )
    context = RowsContextPort(
        _ROWS,
        facts=(
            _fact("cal-1", FactKind.CALENDAR_SNAPSHOT, {"fetched": True, "blocks": 3}),
            _fact("con-1", FactKind.ACTIVE_CONSTRAINTS, {"fetched": True, "count": 1}),
        ),
    )
    kernel = _kernel(repo, planner, context=context)

    first = await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    assert isinstance(first, AwaitingApproval)
    assert first.question is not None

    outcome = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.approve",
            actor_user_id="U1",
            expected_revision=4,
            intent=ApproveArtifact(
                artifact_id=first.artifact.artifact_id,
                artifact_revision=first.artifact.revision,
                artifact_digest=first.artifact.digest,
            ),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(outcome, AwaitingApproval), outcome
    assert outcome.artifact.kind.value == "validated_candidate"
    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert any(a.artifact_id == first.artifact.artifact_id for a in saved.approvals)
    # The candidate outcome carries no question of its own -- the skeleton's
    # was never answered, and `_release_question` must not keep holding it
    # once a different outcome has superseded it.
    assert saved.pending_blocker is None


@pytest.mark.asyncio
async def test_re_presenting_the_skeleton_keeps_the_riding_question() -> None:
    """The card is redrawn; the question is redrawn with it.

    Any turn that lands on the same unapproved skeleton -- `Advance` (the
    "Try that again" button on a transient failure, and `NextControl`) or a
    fresh `StartSession` -- short-circuits at `_pending_approval`. That branch
    used to answer with the artifact alone, `_release_question` saw no
    question and cleared the held record, and the redrawn card had no question
    on it while the card that did was receipted with its buttons stripped. The
    question vanished, and a press on the old card refused as
    `stale_blocker_choice`. No log line said so.
    """

    options = [
        BlockerOption(
            option_id="read-1",
            label="Agent analysis",
            effect="titles the block 'Agent analysis'",
        )
    ]
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(
        _skeleton_with(
            [
                UserBlockerDraft(
                    requirement_id="skeleton.activity_reading",
                    why_needed="'agent-in-ysis' is not a name I can read",
                    blocking=False,
                    options=options,
                )
            ]
        ),
        # Never reached: the second turn short-circuits on the unapproved
        # skeleton and asks no planner. Scripted anyway so a regression that
        # *does* call one fails here rather than raising StopIteration.
        _skeleton_citing("a1"),
    )
    kernel = _kernel(repo, planner, context=RowsContextPort(_ROWS))

    first = await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    assert isinstance(first, AwaitingApproval)
    assert first.question is not None

    again = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.retry",
            actor_user_id="U1",
            expected_revision=4,
            intent=Advance(),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(again, AwaitingApproval), again
    assert again.artifact.artifact_id == first.artifact.artifact_id
    assert again.question is not None
    assert again.question.requirement_id == "skeleton.activity_reading"
    # The options are the ones that were offered, from the held record --
    # the catalog has none to re-derive.
    assert [o.option_id for o in again.question.options] == ["read-1"]

    saved = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert saved.pending_blocker is not None
    assert saved.pending_blocker.requirement_id == "skeleton.activity_reading"


@pytest.mark.asyncio
async def test_a_re_present_raises_no_question_once_it_is_answered() -> None:
    """The exemption is scoped to a requirement that is still open.

    A record left standing after its requirement closed would redraw an
    answered question and let a second press file a second answer against it.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _ScriptedPlanner(
        _skeleton_with(
            [
                UserBlockerDraft(
                    requirement_id="skeleton.activity_reading",
                    why_needed="'agent-in-ysis' is not a name I can read",
                    blocking=False,
                    options=[
                        BlockerOption(
                            option_id="read-1",
                            label="Agent analysis",
                            effect="titles the block 'Agent analysis'",
                        )
                    ],
                )
            ]
        ),
        _skeleton_citing("a1"),
    )
    kernel = _kernel(repo, planner, context=RowsContextPort(_ROWS))

    first = await kernel.turn(_advance_request(), progress=RecordingProgressSink())
    assert isinstance(first, AwaitingApproval)

    answered = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.press",
            actor_user_id="U1",
            expected_revision=4,
            intent=ChooseBlockerOption(
                requirement_id="skeleton.activity_reading", option_id="read-1"
            ),
        ),
        progress=RecordingProgressSink(),
    )
    assert isinstance(answered, AwaitingApproval), answered

    again = await kernel.turn(
        TurnRequest(
            session_key="C1:1.0",
            interaction_id="1772.retry",
            actor_user_id="U1",
            expected_revision=5,
            intent=Advance(),
        ),
        progress=RecordingProgressSink(),
    )

    assert isinstance(again, AwaitingApproval), again
    assert again.question is None
