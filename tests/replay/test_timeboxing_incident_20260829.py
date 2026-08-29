"""Replay the 2026-08-29 Slack timeboxing session against recorded inputs.

On that Saturday a planning session went wrong in three separate ways, and
each of them is the kind of failure that comes back quietly:

* Saturday 2026-08-29 was answered as a Friday, and a weekend as a working
  day, because the planner re-derived the day from the work it found on the
  calendar. A day full of meetings looks like a working day, so the wrong
  answer was also the plausible one.
* The user delegated the ordinary placements -- "you plan those things" --
  and was asked for an exact gym time and a morning start anyway. A
  planner-owned gap became a user question, which is the one conversion this
  design exists to forbid.
* The advance that should have produced a skeleton produced another recap, so
  the session could be advanced forever without ever reaching something
  reviewable.

The replay drives the real kernel, the real requirement catalog and the real
contracts. Only the planner, the host context and the commit port are doubles,
because those are the seams the incident's evidence was recorded at.

Two rules shape how the assertions are written.

**Nothing here reads prose.** "No outcome asks for gym time" is expressed over
requirement IDs and their catalog owners -- identifiers this system minted --
never by looking for words in a question. Searching question text for "gym"
would pass the moment the wording changed and would be a judgement about
meaning made by a pattern, which is the project's central prohibition. The
structural form also catches more: it fails if the catalog reclassifies gym
placement as user-owned, which is the actual regression.

**The fixture is validated by the production contracts.** Every recorded fact,
intent and planner result is parsed through the same strict Pydantic models the
kernel uses, so a fixture that drifts from the contracts fails loudly instead of
replaying something the system can no longer receive. `extra="forbid"` is also
what keeps a stray credential or raw payload from riding along in a recording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    CommitPort,
    InMemoryPlanningSessionRepository,
    PlannerPort,
    PlanningBrief,
    ProgressSink,
    TurnRequest,
)
from fateforger.agents.timeboxing.readiness import (
    RequirementOwner,
    TimeboxRequirements,
)
from fateforger.agents.timeboxing.session_contracts import (
    ApproveArtifact,
    ArtifactKind,
    ArtifactReady,
    AwaitingApproval,
    AwaitingUser,
    Committed,
    DayType,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
    TimeboxIntent,
    TurnFailed,
    TurnOutcome,
)
from fateforger.slack_bot.progress_events import ProgressStatus

# The kernel-side doubles already written for Task 4. Reused rather than
# reimplemented: a second recording sink with slightly different semantics is
# how two tests come to disagree about what "was emitted" means.
from tests.unit.test_adaptive_timeboxing import (
    RecordedContextPort,
    RecordingProgressSink,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "timeboxing_incident_20260829.json"

_INTENT_ADAPTER: TypeAdapter[TimeboxIntent] = TypeAdapter(TimeboxIntent)

# `STARTED` is the only non-terminal status the progress vocabulary has. The
# other three all end an activity, and which one it ended with is not this
# test's business -- only that the row stopped spinning.
_TERMINAL_STATUSES = frozenset(
    {
        ProgressStatus.SUCCEEDED.value,
        ProgressStatus.FAILED.value,
        ProgressStatus.SUPERSEDED.value,
    }
)


def load_fixture() -> dict[str, Any]:
    """Read the sanitized recording of the incident."""

    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _as_model(model: Any, data: Any) -> Any:
    """Validate recorded JSON through a strict production contract.

    The contracts run Pydantic in strict mode, where an ISO date string is only
    accepted by the JSON validator. Round-tripping through `json.dumps` is what
    lets a recording stay JSON and still be checked by the model that will
    receive it.
    """

    return model.model_validate_json(json.dumps(data))


class ScriptedPlanner(PlannerPort):
    """Return the result the incident recorded for the turn under replay.

    Armed per turn rather than queued, so a kernel that calls the planner on a
    turn the recording has no result for fails here -- naming the extra call --
    instead of silently consuming the next turn's answer and failing three
    assertions later on the wrong subject.
    """

    def __init__(self, recorded: dict[str, Any]) -> None:
        self._recorded = recorded
        self._armed: str | None = None
        self._unavailable = False
        self.briefs: list[PlanningBrief] = []

    def arm(self, key: str | None, *, unavailable: bool = False) -> None:
        self._armed = key
        self._unavailable = unavailable

    @property
    def calls(self) -> int:
        return len(self.briefs)

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        if self._unavailable:
            self.briefs.append(brief)
            self._unavailable = False
            # No provider text. A recording that carried the real one would put
            # a provider's stack trace in the repository.
            raise RuntimeError("the recorded harness run did not answer")
        if self._armed is None:
            raise AssertionError(
                "the kernel called the planner on a turn the recording has no "
                f"planner result for (target {brief.target_artifact.value})"
            )
        self.briefs.append(brief)
        result = _as_model(PlanningResult, self._recorded[self._armed])
        self._armed = None
        return result


class RecordingCommitPort(CommitPort):
    """Record the attempt, then refuse it.

    The plan asks for `commit_port.calls == []`, and a port that only raised
    would report a wrong commit as a `TurnFailed` several frames downstream --
    true, but pointing at the wrong thing. Recording first makes the failing
    assertion name the calendar write that should not have been attempted.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        self.calls.append(digest)
        raise AssertionError("no replayed scenario is allowed to reach a calendar")


@dataclass
class ReplayRun:
    """Everything one replayed scenario is allowed to be judged on."""

    scenario: str
    snapshot: PlanningSessionSnapshot
    outcomes: list[TurnOutcome]
    planner: ScriptedPlanner
    commit: RecordingCommitPort
    progress: RecordingProgressSink
    artifacts: dict[str, PlanningArtifact] = field(default_factory=dict)
    revisions: dict[str, int] = field(default_factory=dict)
    planner_calls_after_turn: list[int] = field(default_factory=list)


def _intent_for(
    turn: dict[str, Any],
    *,
    fixture: dict[str, Any],
    artifacts: dict[str, PlanningArtifact],
) -> TimeboxIntent:
    """Build one typed intent from a recorded turn.

    Days and captured inputs are recorded once at the top of the fixture and
    referred to by name, because six scenarios replay the same Saturday and the
    same captured inputs -- copied into every turn, they would drift, and a
    replay of a day nobody had is worth nothing.

    An approval is the exception that cannot be recorded at all: it names an
    artifact identity that only exists once this run has produced it, so it
    refers to an artifact an earlier turn recorded under a label.
    """

    label = turn.get("approves_recorded_artifact")
    if label is not None:
        artifact = artifacts[label]
        return ApproveArtifact(
            artifact_id=artifact.artifact_id,
            artifact_revision=artifact.revision,
            artifact_digest=artifact.digest,
        )

    day_label = turn.get("confirms_recorded_day")
    if day_label is not None:
        return _INTENT_ADAPTER.validate_json(
            json.dumps(
                {"kind": "confirm_planning_day", "planning_day": fixture[day_label]}
            )
        )

    facts_label = turn.get("provides_recorded_facts")
    if facts_label is not None:
        return _INTENT_ADAPTER.validate_json(
            json.dumps(
                {
                    "kind": "provide_planning_facts",
                    "facts": fixture["captured_facts"][facts_label],
                }
            )
        )

    return _INTENT_ADAPTER.validate_json(json.dumps(turn["intent"]))


def _expected_revision(turn: dict[str, Any], revisions: dict[str, int]) -> int | None:
    """Resolve the revision a recorded turn was stamped with.

    A harness result carries the revision its turn started at. Replaying that
    means stamping a later turn with a revision recorded earlier, which is why
    the recording can name one instead of only hard-coding it.
    """

    label = turn.get("expected_revision_recorded_as")
    if label is not None:
        return revisions[label]
    return turn.get("expected_revision")


async def replay_scenario(
    name: str, fixture: dict[str, Any] | None = None
) -> ReplayRun:
    """Drive the real kernel through one recorded scenario, turn by turn."""

    fixture = fixture if fixture is not None else load_fixture()
    scenario = fixture["scenarios"][name]
    session_key = fixture["session_key"]
    owner_user_id = fixture["owner_user_id"]

    repository = InMemoryPlanningSessionRepository()
    planner = ScriptedPlanner(fixture["planner_results"])
    commit = RecordingCommitPort()
    progress = RecordingProgressSink()
    kernel = AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=RecordedContextPort(),
        commit=commit,
    )

    run = ReplayRun(
        scenario=name,
        snapshot=PlanningSessionSnapshot.new(
            session_key=session_key, owner_user_id=owner_user_id
        ),
        outcomes=[],
        planner=planner,
        commit=commit,
        progress=progress,
    )

    for turn in scenario["turns"]:
        planner.arm(
            turn.get("planner_result"),
            unavailable=bool(turn.get("planner_unavailable")),
        )
        outcome = await kernel.turn(
            TurnRequest(
                session_key=session_key,
                interaction_id=turn["interaction_id"],
                actor_user_id=owner_user_id,
                expected_revision=_expected_revision(turn, run.revisions),
                intent=_intent_for(turn, fixture=fixture, artifacts=run.artifacts),
            ),
            progress=progress,
        )
        planner.arm(None)
        run.outcomes.append(outcome)
        run.planner_calls_after_turn.append(planner.calls)

        current = await repository.load_or_create(
            session_key, owner_user_id=owner_user_id
        )
        artifact_label = turn.get("record_artifact_as")
        if artifact_label is not None:
            assert isinstance(outcome, AwaitingApproval), (
                f"turn {turn['interaction_id']} was recorded as producing an "
                f"artifact and produced {outcome.kind} instead"
            )
            run.artifacts[artifact_label] = outcome.artifact
        revision_label = turn.get("record_revision_as")
        if revision_label is not None:
            run.revisions[revision_label] = current.revision

    run.snapshot = await repository.load_or_create(
        session_key, owner_user_id=owner_user_id
    )
    return run


def requirement_owners(
    snapshot: PlanningSessionSnapshot,
) -> dict[str, RequirementOwner]:
    """Ask the live catalog who owns each requirement, for this snapshot.

    Read from `TimeboxRequirements` rather than restated here, so a test that
    says "gym is planner-owned" is asserting the catalog's answer instead of
    its own copy of it.
    """

    requirements = TimeboxRequirements()
    return {
        gap.requirement_id: gap.owner
        for kind in ArtifactKind
        for gap in requirements.evaluate(kind, snapshot).gaps
    }


def asked_requirement_ids(outcomes: list[TurnOutcome]) -> set[str]:
    """Every requirement the session put to the user, by ID."""

    return {
        outcome.requirement_id
        for outcome in outcomes
        if isinstance(outcome, AwaitingUser)
    }


def artifact_kinds(outcomes: list[TurnOutcome]) -> list[ArtifactKind]:
    """The artifacts the replayed turns actually put in front of the user."""

    kinds: list[ArtifactKind] = []
    for outcome in outcomes:
        if isinstance(outcome, (AwaitingApproval, ArtifactReady)):
            kinds.append(outcome.artifact.kind)
        elif isinstance(outcome, Committed):
            kinds.append(outcome.receipt.kind)
    return kinds


def assumption_requirement_ids(snapshot: PlanningSessionSnapshot) -> set[str]:
    """Every requirement the planner decided for itself and recorded."""

    return {assumption.requirement_id for assumption in snapshot.assumptions}


def activity_statuses(events: list[object], phase: str) -> list[str]:
    """The statuses one progress activity reported, in order."""

    return [
        event["status"]
        for event in events
        if isinstance(event, dict) and event.get("phase") == phase
    ]


def unterminated_activities(events: list[object]) -> set[str]:
    """Progress activities that started and never reached a terminal status.

    A started row renders as a spinner. `ProgressChannel.close()` deliberately
    refuses to tick it -- "a step that never reported done did not finish" --
    so an activity left open is a spinner that outlives the turn.
    """

    open_activities: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        phase = event.get("phase")
        status = event.get("status")
        if not isinstance(phase, str) or not isinstance(status, str):
            continue
        if status == ProgressStatus.STARTED.value:
            open_activities.add(phase)
        elif status in _TERMINAL_STATUSES:
            open_activities.discard(phase)
    return open_activities


# --------------------------------------------------------------------------
# The incident itself
# --------------------------------------------------------------------------


async def test_the_replayed_day_is_still_saturday_the_twenty_ninth() -> None:
    """Catches a planning day re-derived from what the calendar contained.

    The incident answered 2026-08-29 as a Friday working day because the
    planner looked at a day full of work and concluded the day must be a
    working one. Weekday and day type are arithmetic on a locked date here, so
    no amount of work on the calendar can move them.
    """

    fixture = load_fixture()
    run = await replay_scenario("incident", fixture)

    recorded_day = _as_model(PlanningDay, fixture["planning_day"])
    assert run.snapshot.planning_day is not None
    assert run.snapshot.planning_day.date.isoformat() == "2026-08-29"
    assert run.snapshot.planning_day.iso_weekday == 6
    assert run.snapshot.planning_day.day_type is DayType.WEEKEND
    # The recording's own statement of the locked day and the day the session
    # ended on have to be the same object, or the replay proves nothing about
    # the incident it claims to replay.
    assert run.snapshot.planning_day == recorded_day


async def test_the_gym_placement_is_a_labelled_planner_owned_assumption() -> None:
    """Catches a gym time that the session never decided and never recorded.

    "You plan those things" is a delegation. Honouring it means choosing a time
    *and* saying so: an unlabelled choice is indistinguishable from a guess the
    user never got to see, and the label is what makes it correctable.
    """

    run = await replay_scenario("incident")

    assert "skeleton.gym_placement" in assumption_requirement_ids(run.snapshot)
    gym = [
        assumption
        for assumption in run.snapshot.assumptions
        if assumption.requirement_id == "skeleton.gym_placement"
    ]
    assert len(gym) == 1
    assert gym[0].value is not None
    assert gym[0].why_needed
    assert requirement_owners(run.snapshot)["skeleton.gym_placement"] is (
        RequirementOwner.PLANNER
    )


async def test_no_turn_asks_the_user_for_a_planner_owned_placement() -> None:
    """Catches the incident's central conversion: a planner gap became a question.

    The user was asked for an exact gym time and for a morning start after
    delegating both. Those are `skeleton.gym_placement` and
    `skeleton.ordinary_placement`, and both are planner-owned.

    Deliberately structural. Searching the question text for "gym" would be a
    judgement about meaning made by a pattern, it would pass the moment the
    copy was reworded, and it would miss the regression that matters -- the
    catalog quietly reclassifying the requirement. Pinning the ownership and
    then asserting over IDs catches both halves.
    """

    run = await replay_scenario("incident")
    owners = requirement_owners(run.snapshot)

    assert owners["skeleton.gym_placement"] is RequirementOwner.PLANNER
    assert owners["skeleton.ordinary_placement"] is RequirementOwner.PLANNER

    asked = asked_requirement_ids(run.outcomes)
    not_the_user_s = {
        requirement_id
        for requirement_id, owner in owners.items()
        if owner is not RequirementOwner.USER
    }
    assert asked.isdisjoint(not_the_user_s)


async def test_the_only_question_asked_is_hard_and_user_owned() -> None:
    """Catches ceremonial questions padded around a real one.

    Every question costs a round trip in a chat thread, so the invariant is not
    just "do not ask the wrong thing" but "ask only what genuinely blocks the
    next artifact".
    """

    run = await replay_scenario("incident")
    requirements = TimeboxRequirements()

    asked = asked_requirement_ids(run.outcomes)
    assert asked == {"skeleton.requested_activity"}
    gap = requirements.evaluate(ArtifactKind.SKELETON, run.snapshot).by_id(
        "skeleton.requested_activity"
    )
    assert gap.owner is RequirementOwner.USER
    assert gap.hard is True


async def test_the_advance_ends_on_a_skeleton_not_another_recap() -> None:
    """Catches an advance that produces prose and leaves the session where it was.

    In the incident, "you plan those things" returned a summary of what had
    been said. A recap is not reviewable: there is nothing to approve and
    nothing to revise, so the session can be advanced indefinitely.
    """

    run = await replay_scenario("incident")

    produced = artifact_kinds(run.outcomes)
    assert produced, "the replayed session produced no artifact at all"
    assert produced[-1] is ArtifactKind.SKELETON
    assert isinstance(run.outcomes[-1], AwaitingApproval)


async def test_nothing_in_the_replay_reaches_a_calendar() -> None:
    """Catches a commit before the approval that is supposed to gate it.

    Stage 3 presents. The candidate gate, and the commit behind it, are two
    approvals further on, and the fastest way to lose trust in this feature is
    for a planning conversation to write to a real calendar.
    """

    run = await replay_scenario("incident")

    assert run.commit.calls == []
    assert run.snapshot.status == "open"
    assert not [
        artifact
        for artifact in run.snapshot.artifacts
        if artifact.kind is ArtifactKind.COMMIT_RECEIPT
    ]


async def test_every_progress_activity_reaches_a_terminal_state() -> None:
    """Catches a progress row that spins forever after its turn has ended.

    A dead session and a slow one look identical in Slack; the checklist is the
    only thing that tells them apart, and it can only do that if every activity
    it starts is eventually resolved.
    """

    run = await replay_scenario("incident")

    assert unterminated_activities(run.progress.events) == set()


async def test_every_recorded_input_validates_against_the_typed_contracts() -> None:
    """Catches a recording that has drifted from what the system can receive.

    The contracts forbid unknown fields, so this is also the hygiene check the
    plan asks for: a token, a raw calendar payload or a stray reasoning blob
    cannot ride along in a recorded fact or planner result without failing
    here.
    """

    fixture = load_fixture()

    _as_model(PlanningDay, fixture["planning_day"])
    _as_model(PlanningDay, fixture["relocked_planning_day"])
    for facts in fixture["captured_facts"].values():
        for fact in facts:
            _as_model(PlanningFact, fact)
    for result in fixture["planner_results"].values():
        _as_model(PlanningResult, result)
    for scenario in fixture["scenarios"].values():
        for turn in scenario["turns"]:
            if "intent" in turn:
                _INTENT_ADAPTER.validate_json(json.dumps(turn["intent"]))


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------


async def test_a_hard_user_owned_blocker_is_asked_once_before_any_planner_runs() -> (
    None
):
    """Catches a planner asked to invent the one thing only the user knows.

    A day with nothing the user wants out of it is the genuine conflict: no
    placement the planner could choose resolves it, and one answer unblocks
    every downstream artifact. So it is asked, exactly once, before a planner
    is consulted -- and once answered the same session proceeds without asking
    again.
    """

    run = await replay_scenario("hard_conflict")

    assert isinstance(run.outcomes[0], AwaitingUser)
    assert run.outcomes[0].requirement_id == "skeleton.requested_activity"
    assert run.outcomes[0].why_needed
    # Nothing was planned before the question, and nothing was asked after it.
    assert run.planner_calls_after_turn[0] == 0
    assert len(asked_requirement_ids(run.outcomes)) == 1
    assert isinstance(run.outcomes[-1], AwaitingApproval)
    assert run.outcomes[-1].artifact.kind is ArtifactKind.SKELETON


async def test_the_planner_may_not_hand_a_planner_owned_gap_back_as_a_question() -> (
    None
):
    """Catches the incident's mechanism, at the seam where it entered.

    The recorded planner does exactly what DeepSeek did on 2026-08-29: it
    returns "ask the user for a gym time" instead of choosing one. The kernel
    refuses it rather than passing it on, because a prompt cannot be relied on
    to hold a boundary that a single turn can cross.
    """

    run = await replay_scenario("planner_delegation_refused")

    failure = run.outcomes[-1]
    assert isinstance(failure, TurnFailed)
    assert failure.code == "illegal_user_blocker"
    # The refusal is total: nothing was asked, and no half-built skeleton was
    # kept from a result the kernel rejected.
    assert asked_requirement_ids(run.outcomes) == {"skeleton.requested_activity"}
    assert "skeleton.gym_placement" not in asked_requirement_ids(run.outcomes)
    assert not [
        artifact
        for artifact in run.snapshot.artifacts
        if artifact.kind is ArtifactKind.SKELETON
    ]
    assert assumption_requirement_ids(run.snapshot) == set()


async def test_relocking_the_date_discards_the_skeleton_built_for_the_old_day() -> None:
    """Catches a corrected date leaving yesterday's plan standing.

    Correcting the day is the one edit that invalidates everything downstream.
    A skeleton that survives the correction is worse than no skeleton: it is a
    plan for a day nobody is planning any more, and it still carries whatever
    approval it had.
    """

    fixture = load_fixture()
    run = await replay_scenario("date_relock", fixture)

    relocked = _as_model(PlanningDay, fixture["relocked_planning_day"])
    assert run.snapshot.planning_day == relocked
    assert run.snapshot.planning_day.date == date(2026, 8, 31)
    assert run.snapshot.planning_day.iso_weekday == 1
    assert run.snapshot.planning_day.day_type is DayType.WORKING

    superseded = run.artifacts["skeleton_v1"]
    skeletons = [
        artifact
        for artifact in run.snapshot.artifacts
        if artifact.kind is ArtifactKind.SKELETON
    ]
    assert len(skeletons) == 1
    assert skeletons[0].artifact_id != superseded.artifact_id
    assert superseded.artifact_id not in {
        approval.artifact_id for approval in run.snapshot.approvals
    }
    # The replanned skeleton was briefed on the corrected day, not the old one.
    assert run.planner.briefs[-1].locked_day == relocked


async def test_a_duplicate_slack_delivery_replays_its_outcome_and_plans_once() -> None:
    """Catches Slack's at-least-once delivery turning into two plans.

    The same event arrives twice carrying the same identifier and the revision
    it was drawn at. Replaying the stored outcome is the only answer that costs
    nothing and changes nothing; planning again would spend a harness run and
    leave two skeletons where the user made one request.
    """

    run = await replay_scenario("duplicate_delivery")

    assert run.outcomes[1] == run.outcomes[2]
    assert run.planner.calls == 1
    assert run.revisions["after_first_delivery"] == run.revisions["after_duplicate"]
    assert (
        len(
            [
                artifact
                for artifact in run.snapshot.artifacts
                if artifact.kind is ArtifactKind.SKELETON
            ]
        )
        == 1
    )


async def test_a_stale_skeleton_approval_approves_neither_skeleton() -> None:
    """Catches an old button carrying its approval onto a plan nobody read.

    Slack keeps a card alive forever. When a later reply replaced the skeleton,
    the Proceed button still sitting in the thread points at a plan the session
    has discarded. Retargeting the press at whatever is current would look like
    a working approval and be one nobody gave.
    """

    run = await replay_scenario("stale_skeleton_approval")

    failure = run.outcomes[-1]
    assert isinstance(failure, TurnFailed)
    assert failure.code == "stale_approval"

    superseded = run.artifacts["skeleton_v1"]
    approved = {approval.artifact_id for approval in run.snapshot.approvals}
    current = [
        artifact
        for artifact in run.snapshot.artifacts
        if artifact.kind is ArtifactKind.SKELETON
    ]
    assert len(current) == 1
    assert superseded.artifact_id not in approved
    assert current[0].artifact_id not in approved
    assert run.commit.calls == []


async def test_a_newer_user_turn_supersedes_an_older_harness_result() -> None:
    """Catches a slow harness answer overwriting what the user did meanwhile.

    A one-shot harness run carries the revision it started at. If the user
    replies while it is still running, the answer that eventually lands is
    about a session state that no longer exists. The stamped revision is what
    makes that decidable without reading either message.
    """

    run = await replay_scenario("superseded_harness_result")

    late = run.outcomes[-1]
    assert isinstance(late, TurnFailed)
    assert late.code == "stale_session_revision"
    # The late arrival changed nothing: no extra plan, no new revision.
    assert run.planner.calls == 1
    assert run.snapshot.revision == run.revisions["after_user_turn"]
    assert (
        len(
            [
                artifact
                for artifact in run.snapshot.artifacts
                if artifact.kind is ArtifactKind.SKELETON
            ]
        )
        == 1
    )


async def test_an_unreachable_planner_fails_the_turn_without_asking_anything() -> None:
    """Catches a dependency outage escaping as a question or a half-plan.

    A harness run that does not answer is a system-owned failure. Turning it
    into a question would make an outage indistinguishable from a genuine gap
    in what the user told us, and the user would answer something they had
    already answered.
    """

    run = await replay_scenario("planner_unavailable")

    failure = run.outcomes[-1]
    assert isinstance(failure, TurnFailed)
    assert failure.code == "dependency_unavailable"
    assert artifact_kinds(run.outcomes) == []
    assert asked_requirement_ids(run.outcomes) == {"skeleton.requested_activity"}
    assert run.commit.calls == []


async def test_a_failed_turn_does_not_report_its_planning_step_as_succeeded() -> None:
    """Catches a green tick on work that did not happen.

    The checklist is the only thing in Slack that distinguishes a dead session
    from a slow one. A row that reports success for a turn that failed is worse
    than a row left spinning: the spinner is merely unfinished, the tick is
    wrong, and the user has no reason to look further.
    """

    run = await replay_scenario("planner_unavailable")

    assert isinstance(run.outcomes[-1], TurnFailed)
    assert ProgressStatus.SUCCEEDED.value not in activity_statuses(
        run.progress.events, "planning"
    )
