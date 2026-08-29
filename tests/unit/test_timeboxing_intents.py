from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    InMemoryPlanningSessionRepository,
    TurnRequest,
)
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactKind,
    BlockerOption,
    ChooseBlockerOption,
    ConfirmPlanningDay,
    DayType,
    FactKind,
    PendingBlocker,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
    TurnFailed,
)
from fateforger.slack_bot.timeboxing_commit import TimeboxCommitMeta
from fateforger.slack_bot.timeboxing_intents import (
    ArtifactActionMeta,
    InterpretedTimeboxTurn,
    TimeboxingIntentInterpreter,
    intent_from_artifact_action,
    intent_from_date_action,
)


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


class _ForbiddenDependency:
    def __getattr__(self, name: str):
        raise AssertionError(f"kernel dependency must not be used: {name}")


class _ProgressSink:
    async def emit(self, _event: object) -> None:
        return None


def _planning_day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 8, 29),
        timezone="Europe/Amsterdam",
        lock_revision=1,
    )


def _skeleton() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="skeleton-1",
        kind=ArtifactKind.SKELETON,
        revision=2,
        payload={"markdown": "## Saturday\n- 17:00 Gym"},
        dependency_revisions={"planning_day": 1},
    )


def _capture_snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=_planning_day(),
    )


def _snapshot_with_skeleton() -> PlanningSessionSnapshot:
    return _capture_snapshot().model_copy(update={"artifacts": [_skeleton()]})


def _artifact_action(*, expected_revision: int = 3) -> ArtifactActionMeta:
    skeleton = _skeleton()
    return ArtifactActionMeta(
        schema_version=1,
        session_key="C1:1.0",
        expected_revision=expected_revision,
        decision="approve",
        artifact_id=skeleton.artifact_id,
        artifact_revision=skeleton.revision,
        artifact_digest=skeleton.digest,
    )


def _kernel(snapshot: PlanningSessionSnapshot) -> AdaptiveTimeboxing:
    return AdaptiveTimeboxing(
        repository=InMemoryPlanningSessionRepository([snapshot]),
        requirements=TimeboxRequirements(),
        planner=_ForbiddenDependency(),
        context=_ForbiddenDependency(),
        commit=_ForbiddenDependency(),
    )


@pytest.mark.asyncio
async def test_proceed_during_capture_becomes_advance() -> None:
    """Catches capture-stage consent being misbound to a nonexistent artifact."""

    client = _SchemaOutputClient({"decision": "advance", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)

    intent = await interpreter.interpret(
        "you plan those things", _capture_snapshot()
    )

    assert intent == Advance()
    assert client.calls[0][1] is InterpretedTimeboxTurn


@pytest.mark.asyncio
async def test_proceed_beside_skeleton_binds_only_trusted_artifact_identity() -> None:
    """Catches model-provided or missing identity controlling an approval."""

    skeleton = _skeleton()
    client = _SchemaOutputClient({"decision": "approve", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)

    intent = await interpreter.interpret(
        "proceed", _snapshot_with_skeleton()
    )

    assert intent == ApproveArtifact(
        artifact_id="skeleton-1",
        artifact_revision=2,
        artifact_digest=skeleton.digest,
    )
    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert "skeleton-1" not in prompt
    assert skeleton.digest not in prompt
    assert '"display_stage":"skeleton"' in prompt
    assert '"pending_artifact_kind":"skeleton"' in prompt


def test_skeleton_button_and_nl_produce_same_approval_intent() -> None:
    """Catches Block Kit approval taking a separate domain path from NL."""

    skeleton = _skeleton()

    action = intent_from_artifact_action(_artifact_action().model_dump_json())

    assert action is not None
    assert action.intent == ApproveArtifact(
        artifact_id="skeleton-1",
        artifact_revision=2,
        artifact_digest=skeleton.digest,
    )
    assert action.session_key == "C1:1.0"
    assert action.expected_revision == 3


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        json.dumps({"schema_version": 1, "decision": "approve"}),
        json.dumps(
            {
                "schema_version": 1,
                "session_key": "C1:1.0",
                "expected_revision": 3,
                "decision": "approve",
                "artifact_id": "skeleton-1",
                "artifact_revision": 2,
                "artifact_digest": "forged",
            }
        ),
    ],
)
def test_malformed_artifact_metadata_yields_no_intent(payload: str) -> None:
    """Catches incomplete or invalid transport fields reaching the kernel."""

    assert intent_from_artifact_action(payload) is None


@pytest.mark.asyncio
async def test_valid_old_artifact_action_is_typed_then_rejected_as_stale() -> None:
    """Catches structural decoding bypassing kernel revision enforcement."""

    action = ArtifactActionMeta.model_validate_json(
        _artifact_action(expected_revision=2).model_dump_json()
    )
    envelope = intent_from_artifact_action(action)
    assert envelope is not None
    assert isinstance(envelope.intent, ApproveArtifact)

    outcome = await _kernel(_snapshot_with_skeleton()).turn(
        TurnRequest(
            session_key=envelope.session_key,
            interaction_id="action-stale",
            actor_user_id="U1",
            expected_revision=envelope.expected_revision,
            intent=envelope.intent,
        ),
        progress=_ProgressSink(),
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "stale_session_revision"


@pytest.mark.asyncio
async def test_non_owner_artifact_action_is_rejected_before_approval() -> None:
    """Catches trusted card metadata being treated as user authorization."""

    action = _artifact_action()
    envelope = intent_from_artifact_action(action)
    assert envelope is not None
    assert isinstance(envelope.intent, ApproveArtifact)
    kernel = _kernel(_snapshot_with_skeleton())

    outcome = await kernel.turn(
        TurnRequest(
            session_key=envelope.session_key,
            interaction_id="action-wrong-owner",
            actor_user_id="U2",
            expected_revision=envelope.expected_revision,
            intent=envelope.intent,
        ),
        progress=_ProgressSink(),
    )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "session_owner_mismatch"


def test_date_selection_changes_only_date_in_versioned_metadata() -> None:
    """Catches a date-card refresh dropping session or revision identity."""

    original = TimeboxCommitMeta(
        schema_version=1,
        session_key="C1:1.0",
        expected_revision=7,
        user_id="U1",
        channel_id="C1",
        thread_ts="1.0",
        date="2026-08-29",
        tz="Europe/Amsterdam",
    )

    changed = original.with_selected_date("2026-08-30")

    assert changed.model_dump() == {
        **original.model_dump(),
        "date": "2026-08-30",
    }
    assert TimeboxCommitMeta.from_value(changed.to_value()) == changed


def test_legacy_date_metadata_remains_decodable() -> None:
    """Catches versioning making the existing Stage-0 backend inert."""

    legacy = (
        "channel_id=C1&thread_ts=1.0&user_id=U1&date=2026-08-29"
        "&tz=Europe%2FAmsterdam"
    )

    parsed = TimeboxCommitMeta.from_value(legacy)

    assert parsed is not None
    assert parsed.schema_version == 1
    assert parsed.session_key == "C1:1.0"
    assert parsed.expected_revision == 0


def test_explicit_v1_date_metadata_requires_session_key() -> None:
    """Catches legacy session defaults weakening an explicit v1 payload."""

    value = (
        "schema_version=1&expected_revision=7&user_id=U1&channel_id=C1"
        "&thread_ts=1.0&date=2026-08-29&tz=Europe%2FAmsterdam"
    )

    assert TimeboxCommitMeta.from_value(value) is None


def test_explicit_v1_date_metadata_requires_expected_revision() -> None:
    """Catches legacy revision defaults weakening an explicit v1 payload."""

    value = (
        "schema_version=1&session_key=C1%3A1.0&user_id=U1&channel_id=C1"
        "&thread_ts=1.0&date=2026-08-29&tz=Europe%2FAmsterdam"
    )

    assert TimeboxCommitMeta.from_value(value) is None


def test_explicit_v1_date_metadata_requires_timezone() -> None:
    """Catches the legacy UTC default weakening an explicit v1 payload."""

    value = (
        "schema_version=1&session_key=C1%3A1.0&expected_revision=7&user_id=U1"
        "&channel_id=C1&thread_ts=1.0&date=2026-08-29"
    )

    assert TimeboxCommitMeta.from_value(value) is None


def test_date_action_derives_typed_weekend_planning_day() -> None:
    """Catches date confirmation relying on model classification or prose."""

    meta = TimeboxCommitMeta(
        schema_version=1,
        session_key="C1:1.0",
        expected_revision=7,
        user_id="U1",
        channel_id="C1",
        thread_ts="1.0",
        date="2026-08-29",
        tz="Europe/Amsterdam",
    )

    action = intent_from_date_action(meta.to_value())

    assert action is not None
    assert action.session_key == "C1:1.0"
    assert action.expected_revision == 7
    assert isinstance(action.intent, ConfirmPlanningDay)
    assert action.intent.planning_day.date == date(2026, 8, 29)
    assert action.intent.planning_day.iso_weekday == 6
    assert action.intent.planning_day.day_type.value == "weekend"
    assert action.intent.planning_day.lock_revision == 8


def test_ui_action_envelope_composes_into_common_turn_request() -> None:
    """Catches the UI adapter forcing the executor to re-decode raw metadata."""

    action = intent_from_artifact_action(_artifact_action().model_dump_json())
    assert action is not None

    request = TurnRequest(
        session_key=action.session_key,
        interaction_id="action-1",
        actor_user_id="U1",
        expected_revision=action.expected_revision,
        intent=action.intent,
    )

    assert request.session_key == "C1:1.0"
    assert request.expected_revision == 3
    assert request.intent == action.intent


def test_malformed_date_action_yields_no_intent() -> None:
    """Catches invalid ISO date metadata becoming a planning-day intent."""

    value = (
        "schema_version=1&session_key=C1%3A1.0&expected_revision=7&user_id=U1"
        "&channel_id=C1&thread_ts=1.0&date=Saturday&tz=Europe%2FAmsterdam"
    )

    assert intent_from_date_action(value) is None


def test_unknown_timezone_date_action_yields_no_intent() -> None:
    """Catches an invalid host timezone entering the locked planning day."""

    value = (
        "schema_version=1&session_key=C1%3A1.0&expected_revision=7&user_id=U1"
        "&channel_id=C1&thread_ts=1.0&date=2026-08-29&tz=Mars%2FBase"
    )

    assert intent_from_date_action(value) is None


def test_an_option_press_becomes_a_typed_choice_bound_to_its_question() -> None:
    """Catches a press arriving as text somebody then has to interpret.

    Both fields are identifiers this system minted, so the whole press crosses
    the boundary as data the host recognises rather than words it has to read.
    """

    envelope = intent_from_artifact_action(
        json.dumps(
            {
                "schema_version": 1,
                "session_key": "C1:1.0",
                "expected_revision": 3,
                "decision": "choose_option",
                "requirement_id": "skeleton.day_shape",
                "option_id": "option-2",
            }
        )
    )

    assert envelope is not None
    assert envelope.intent == ChooseBlockerOption(
        requirement_id="skeleton.day_shape", option_id="option-2"
    )
    assert envelope.expected_revision == 3


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps(
            {
                "schema_version": 1,
                "session_key": "C1:1.0",
                "expected_revision": 3,
                "decision": "choose_option",
                "requirement_id": "skeleton.day_shape",
            }
        ),
        json.dumps(
            {
                "schema_version": 1,
                "session_key": "C1:1.0",
                "expected_revision": 3,
                "decision": "choose_option",
                "option_id": "option-2",
            }
        ),
    ],
)
def test_a_half_named_option_press_yields_no_intent(payload: str) -> None:
    """A press names which question and which answer, or it names neither.

    The kernel checks the pair against the question it is holding. Half a pair
    could only be completed by guessing, and a guessed requirement would file
    the answer against a question the user never saw.
    """

    assert intent_from_artifact_action(payload) is None


def _blocker_snapshot(
    options: list[BlockerOption],
) -> PlanningSessionSnapshot:
    """A session holding one open question, with what it offered against it."""

    return _capture_snapshot().model_copy(
        update={
            "pending_blocker": PendingBlocker(
                requirement_id="skeleton.day_shape",
                fact_kind=FactKind.ORDINARY_PLACEMENT,
                options=options,
            )
        }
    )


def _shape_options() -> list[BlockerOption]:
    return [
        BlockerOption(
            option_id="option-1",
            label="Deep work first",
            effect="puts the gym after dinner",
        ),
        BlockerOption(
            option_id="option-2",
            label="Gym first",
            effect="puts deep work in the evening",
        ),
    ]


@pytest.mark.asyncio
async def test_an_offered_option_can_be_answered_in_words() -> None:
    """Catches a chat-only session having no way to answer an options question.

    Deciding which offered option somebody meant is a judgement about their
    words, so it goes to a model -- which is what CLAUDE.md requires. The banned
    version is the one nobody is writing: comparing the reply to the labels.
    """

    client = _SchemaOutputClient({"decision": "choose_option", "option_id": "option-2"})
    interpreter = TimeboxingIntentInterpreter(client)

    intent = await interpreter.interpret(
        "gym first, then I'll think in the evening",
        _blocker_snapshot(_shape_options()),
    )

    assert intent == ChooseBlockerOption(
        requirement_id="skeleton.day_shape", option_id="option-2"
    )
    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert (
        '"allowed_decisions":["provide_facts","advance","choose_option",'
        '"back","cancel"]'
    ) in prompt
    # The offer is the context the judgement needs: an id with no label beside
    # it asks the model to choose between two names it has never seen.
    assert "Gym first" in prompt
    assert "puts deep work in the evening" in prompt


@pytest.mark.asyncio
async def test_a_typed_choice_can_only_name_an_option_that_was_offered() -> None:
    """The schema is narrowed to the ids on offer, so free text cannot be one.

    An id the model invented would name a question nobody was asked. The kernel
    refuses one anyway -- a schema is a request and a guard is a guarantee --
    but the request is worth making, because a refusal costs the user a turn.
    """

    client = _SchemaOutputClient({"decision": "choose_option", "option_id": "option-1"})
    interpreter = TimeboxingIntentInterpreter(client)

    await interpreter.interpret("the first one", _blocker_snapshot(_shape_options()))

    schema = client.calls[0][1]
    assert schema is not InterpretedTimeboxTurn
    assert issubclass(schema, InterpretedTimeboxTurn)
    with pytest.raises(ValidationError):
        schema.model_validate_json(
            json.dumps({"decision": "choose_option", "option_id": "Gym first"})
        )
    # The ids are minted by position, so `option-3` is a perfectly well-formed
    # one -- it was simply not offered against *this* question. A schema bound
    # to the shape of an id rather than to the offer would let it through, and
    # the answer would land against a choice nobody was shown.
    with pytest.raises(ValidationError):
        schema.model_validate_json(
            json.dumps({"decision": "choose_option", "option_id": "option-3"})
        )


@pytest.mark.asyncio
async def test_an_open_question_still_has_nothing_to_choose_from() -> None:
    """`skeleton.requested_activity` is answered in Hugo's words, not from a list.

    A question that offered nothing has no ids to narrow the schema to, so
    choosing is not among the decisions the turn allows -- and a model that
    claimed it anyway is refused rather than left to invent an id.
    """

    client = _SchemaOutputClient({"decision": "choose_option", "option_id": "option-1"})
    interpreter = TimeboxingIntentInterpreter(client)

    with pytest.raises(ValueError):
        await interpreter.interpret("the first one", _blocker_snapshot([]))

    assert client.calls[0][1] is InterpretedTimeboxTurn
    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert '"allowed_decisions":["provide_facts","advance","back","cancel"]' in prompt


@pytest.mark.asyncio
async def test_choosing_is_not_offered_when_no_question_is_open() -> None:
    """Catches a press-shaped answer arriving where nothing was ever asked."""

    client = _SchemaOutputClient({"decision": "choose_option", "option_id": "option-1"})
    interpreter = TimeboxingIntentInterpreter(client)

    with pytest.raises(ValueError):
        await interpreter.interpret("the first one", _capture_snapshot())

    assert client.calls[0][1] is InterpretedTimeboxTurn
    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert '"allowed_decisions":["provide_facts","advance","back","cancel"]' in prompt


def _proposed_day_artifact() -> PlanningArtifact:
    """The day the host derived and put on the card, awaiting a confirmation."""

    return PlanningArtifact.create(
        artifact_id="planning-day-1",
        kind=ArtifactKind.PLANNING_DAY,
        revision=1,
        payload=_planning_day().model_dump(mode="json"),
        dependency_revisions={},
    )


def _date_stage_snapshot() -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        artifacts=[_proposed_day_artifact()],
    )


@pytest.mark.asyncio
async def test_a_typed_vacation_locks_the_day_as_a_user_override() -> None:
    """Catches a chat-only session having no way past the date card.

    Three of the five day types follow from nothing a calendar can see, so
    without this the only way to say "I am on vacation" is a button -- and an
    agent driving the session by text cannot press one. The basis travels with
    it because `PlanningDay` refuses a `calendar` basis that disagrees with the
    weekday, which is what stops an override from quietly lying about itself.
    """

    client = _SchemaOutputClient(
        {"decision": "confirm_planning_day", "day_type": "vacation", "facts": []}
    )
    interpreter = TimeboxingIntentInterpreter(client)

    intent = await interpreter.interpret(
        "plan Saturday, I'm on vacation", _date_stage_snapshot()
    )

    assert isinstance(intent, ConfirmPlanningDay)
    assert intent.planning_day.date == date(2026, 8, 29)
    assert intent.planning_day.iso_weekday == 6
    assert intent.planning_day.day_type is DayType.VACATION
    assert intent.planning_day.classification_basis == "user_override"
    assert intent.planning_day.lock_revision == 2


@pytest.mark.asyncio
async def test_saying_nothing_about_the_day_keeps_the_weekday_the_host_derived() -> (
    None
):
    """Silence is not a sixth day type, and must not overwrite the default.

    The host derives weekend from the weekday and is right about it. A
    confirmation that carried an override every time would record every day as
    something the user said, which is how `user_override` stops meaning
    anything.
    """

    client = _SchemaOutputClient({"decision": "confirm_planning_day", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)

    intent = await interpreter.interpret("yes, that day", _date_stage_snapshot())

    assert isinstance(intent, ConfirmPlanningDay)
    assert intent.planning_day.day_type is DayType.WEEKEND
    assert intent.planning_day.classification_basis == "calendar"


@pytest.mark.asyncio
async def test_the_date_stage_offers_confirmation_and_never_the_date() -> None:
    """The kind of day is the user's to say; the date is the host's to know.

    The prompt names the decision so the model can take it, and carries no date
    at all -- a model that could see one could return one, and a planning day
    that drifted between the card and the lock is the incident this whole
    session kernel exists to prevent.
    """

    client = _SchemaOutputClient({"decision": "confirm_planning_day", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)

    await interpreter.interpret("go on then", _date_stage_snapshot())

    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert '"confirm_planning_day"' in prompt
    assert "2026-08-29" not in prompt
    assert "date" not in InterpretedTimeboxTurn.model_fields
