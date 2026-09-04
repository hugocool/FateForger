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

    # advance is offered only once the kernel has proposed to close Stage 1.
    # Stage 1 decision set, spec 2026-09-04.
    snapshot = _capture_snapshot().model_copy(update={"stage1": "proposed"})
    intent = await interpreter.interpret("you plan those things", snapshot)

    assert intent == Advance()
    assert client.calls[0][1] is InterpretedTimeboxTurn


@pytest.mark.asyncio
async def test_proceed_beside_skeleton_binds_only_trusted_artifact_identity() -> None:
    """Catches model-provided or missing identity controlling an approval."""

    skeleton = _skeleton()
    client = _SchemaOutputClient({"decision": "approve", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)

    intent = await interpreter.interpret("proceed", _snapshot_with_skeleton())

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
        "channel_id=C1&thread_ts=1.0&user_id=U1&date=2026-08-29&tz=Europe%2FAmsterdam"
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
    assert (  # Stage 1 decision set, spec 2026-09-04
        # No assume: `skeleton.day_shape` is not one of the forty cells, and a
        # `PlannerAssumption` cannot satisfy it.
        '"allowed_decisions":["provide_facts","choose_option","back","cancel"]'
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
    assert (  # Stage 1 decision set, spec 2026-09-04
        '"allowed_decisions":["provide_facts","back","cancel"]'
    ) in prompt


@pytest.mark.asyncio
async def test_choosing_is_not_offered_when_no_question_is_open() -> None:
    """Catches a press-shaped answer arriving where nothing was ever asked."""

    client = _SchemaOutputClient({"decision": "choose_option", "option_id": "option-1"})
    interpreter = TimeboxingIntentInterpreter(client)

    with pytest.raises(ValueError):
        await interpreter.interpret("the first one", _capture_snapshot())

    assert client.calls[0][1] is InterpretedTimeboxTurn
    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert (  # Stage 1 decision set, spec 2026-09-04
        '"allowed_decisions":["provide_facts","back","cancel"]'
    ) in prompt


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

    This once asserted the prompt carried no date at all, on the reasoning that
    a model which could see one could return one. Asking for a different day by
    chat needs the proposed date in the prompt -- "make it Monday" is not
    answerable without knowing what today is -- so the guarantee moved from
    *cannot see* to *cannot say*, which is the stronger of the two and the one
    that was actually load-bearing.

    The schema has no date field. The only channel that can move the day is a
    bounded integer offset, and the host does the arithmetic and re-derives the
    weekday and day type from the result. A planning day that drifted between
    the card and the lock is the incident this session kernel exists to prevent,
    and it drifted because a model named a date -- not because it saw one.
    """

    client = _SchemaOutputClient({"decision": "confirm_planning_day", "facts": []})
    interpreter = TimeboxingIntentInterpreter(client)

    await interpreter.interpret("go on then", _date_stage_snapshot())

    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert '"confirm_planning_day"' in prompt
    assert "2026-08-29" in prompt, "an offset needs something to be measured from"
    assert "date" not in InterpretedTimeboxTurn.model_fields
    offset = InterpretedTimeboxTurn.model_fields["day_offset"]
    assert offset.annotation is not str


@pytest.mark.asyncio
async def test_a_different_day_is_asked_for_by_offset_not_by_date() -> None:
    """Catches the date picker having no chat equivalent.

    The card offers a date dropdown. Chat had nothing, so "make it Monday"
    read as agreement with the proposed day and the session locked the wrong
    date -- silently, because a confirmation looks the same either way.

    The model answers with a distance and the host does the arithmetic, so the
    weekday and day type are re-derived rather than taken on trust. A model
    naming a date directly is the 2026-08-29 incident, and no validation
    catches it: the wrong date is perfectly well formed.
    """

    snapshot = _date_stage_snapshot()
    client = _SchemaOutputClient({"decision": "confirm_planning_day", "day_offset": 2})

    intent = await TimeboxingIntentInterpreter(client).interpret(
        "actually let's do Monday", snapshot
    )

    assert isinstance(intent, ConfirmPlanningDay)
    assert intent.planning_day.date == date(2026, 8, 31)
    assert intent.planning_day.iso_weekday == 1
    # Re-derived, not carried over from the Saturday that was proposed.
    assert intent.planning_day.day_type is DayType.WORKING

    asked = json.loads(client.calls[-1][0][1].content)
    assert asked["proposed_day"] == {
        "date": "2026-08-29",
        "weekday": "Saturday",
        "day_type": "weekend",
    }


@pytest.mark.asyncio
async def test_accepting_the_proposal_shifts_nothing() -> None:
    """Catches an offset applied to every confirmation, quietly."""

    snapshot = _date_stage_snapshot()
    client = _SchemaOutputClient({"decision": "confirm_planning_day"})

    intent = await TimeboxingIntentInterpreter(client).interpret("yes", snapshot)

    assert intent.planning_day.date == date(2026, 8, 29)
    assert intent.planning_day.classification_basis == "calendar"


@pytest.mark.parametrize("offset", [-8, 15, 400])
def test_an_implausible_offset_is_refused_by_the_schema(offset: int) -> None:
    """Catches a well-formed number landing a plan a year away."""

    with pytest.raises(ValidationError):
        InterpretedTimeboxTurn.model_validate(
            {"decision": "confirm_planning_day", "day_offset": offset}
        )


def test_every_control_on_the_date_card_has_its_own_action_id() -> None:
    """Slack refuses the whole message when two controls share an action_id.

    Measured live on 2026-08-30: all five day-type buttons carried
    `ff_timebox_day_type`, Slack answered `invalid_blocks` with no partial
    render, and the bot fell back to truncated text. The visible symptom was a
    date card with no controls at all -- a day nobody could confirm -- and the
    only trace was one `Slack origin update failed ... error=invalid_blocks`
    line. Nothing in the suite looked at the card as Slack does.
    """

    from fateforger.slack_bot.timeboxing_commit import build_timebox_date_card

    card = build_timebox_date_card(
        session_key="C1:1.0",
        expected_revision=1,
        user_id="U1",
        channel_id="C1",
        thread_ts="1.0",
        planned_date="2026-08-30",
        tz_name="Europe/Amsterdam",
    )

    action_ids = [
        element["action_id"]
        for block in card.blocks
        if block.get("type") == "actions"
        for element in block.get("elements", ())
    ]

    assert action_ids, "the date card rendered no controls at all"
    duplicated = sorted({a for a in action_ids if action_ids.count(a) > 1})
    assert not duplicated, f"Slack will reject the whole card: {duplicated}"


class _AttributionRecordingClient:
    """A model client that records the names its call was made under.

    The interpreter is awaited straight from a Slack listener, so AutoGen has
    no agent to stamp on the LLMCall event and every one of its tokens landed
    under agent="unknown". The names have to be in place around ``create``
    itself -- setting them anywhere else records nothing.
    """

    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.agent_id: str | None = None
        self.call_label: str | None = None

    async def create(self, messages, *, json_output):
        from autogen_core._message_handler_context import MessageHandlerContext

        from fateforger.core.llm_attribution import current_call_label

        try:
            self.agent_id = str(MessageHandlerContext.agent_id())
        except RuntimeError:
            self.agent_id = None
        self.call_label = current_call_label()
        return SimpleNamespace(content=json.dumps(self._response))


@pytest.mark.asyncio
async def test_intent_call_is_named_for_the_token_counter() -> None:
    """Catches the interpreter's tokens going back to agent="unknown"."""

    client = _AttributionRecordingClient({"decision": "advance", "facts": []})
    # advance is offered only once the kernel has proposed to close Stage 1.
    # Stage 1 decision set, spec 2026-09-04.
    snapshot = _capture_snapshot().model_copy(update={"stage1": "proposed"})

    await TimeboxingIntentInterpreter(client).interpret("go on", snapshot)

    assert client.call_label == "timebox_intent"
    # The session key rides in the instance key, which is what lets the
    # observability path recover channel and thread for free.
    assert client.agent_id == f"timebox_intent_interpreter/{snapshot.session_key}"


# --- A committed session still listens (#247) -----------------------------------


def _committed_snapshot() -> PlanningSessionSnapshot:
    skeleton = _skeleton()
    candidate = PlanningArtifact.create(
        artifact_id="candidate-1",
        kind=ArtifactKind.VALIDATED_CANDIDATE,
        revision=1,
        payload={"events": []},
        dependency_revisions={"skeleton": 2},
    )
    receipt = PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"committed": True, "tx_id": "tx-1", "candidate_digest": candidate.digest},
        dependency_revisions={"validated_candidate": 1},
    )
    return _capture_snapshot().model_copy(
        update={
            "revision": 5,
            "artifacts": [skeleton, candidate, receipt],
            "status": "committed",
        }
    )


@pytest.mark.asyncio
async def test_a_committed_session_takes_a_revision_bound_to_its_receipt() -> None:
    """Catches the 2026-09-02 dead thread.

    `interpret` raised before asking the model, because the committed stage
    offered no decisions. The thread is the session key, so from the commit
    on every message in it died the same way. The stage must offer a way to
    change the day, and the revision must bind to the receipt -- identity the
    host holds, never text the model wrote.
    """

    from fateforger.agents.timeboxing.session_contracts import ReviseArtifact

    snapshot = _committed_snapshot()
    receipt = next(a for a in snapshot.artifacts if a.kind is ArtifactKind.COMMIT_RECEIPT)
    client = _SchemaOutputClient(
        {
            "decision": "revise",
            "facts": [],
            "revision_instruction": "move all the work two hours later",
        }
    )

    intent = await TimeboxingIntentInterpreter(client).interpret(
        "Can you move all the work stuff 2 hours later", snapshot
    )

    assert intent == ReviseArtifact(
        artifact_id=receipt.artifact_id,
        artifact_revision=receipt.revision,
        artifact_digest=receipt.digest,
        instruction="move all the work two hours later",
    )
    prompt = "\n".join(message.content for message in client.calls[0][0])
    assert '"display_stage":"committed"' in prompt
    assert '"pending_artifact_kind":"commit_receipt"' in prompt
    allowed = json.loads(client.calls[0][0][1].content)["allowed_decisions"]
    assert set(allowed) == {"provide_facts", "revise"}


@pytest.mark.asyncio
async def test_a_committed_session_takes_new_facts() -> None:
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts

    client = _SchemaOutputClient(
        {
            "decision": "provide_facts",
            "facts": [{"kind": "requested_activity", "value": "sleep 00:30-08:30"}],
        }
    )

    intent = await TimeboxingIntentInterpreter(client).interpret(
        "I'll sleep today from 00:30 until 8:30", _committed_snapshot()
    )

    assert isinstance(intent, ProvidePlanningFacts)
    assert [f.kind for f in intent.facts] == [FactKind.REQUESTED_ACTIVITY]


@pytest.mark.asyncio
async def test_a_committed_session_does_not_offer_to_cancel_or_approve() -> None:
    """The model may only pick what the kernel will honour; the schema says so."""

    client = _SchemaOutputClient({"decision": "cancel", "facts": []})

    with pytest.raises(ValueError, match="not allowed in committed"):
        await TimeboxingIntentInterpreter(client).interpret(
            "forget it", _committed_snapshot()
        )


@pytest.mark.asyncio
async def test_a_stated_sleep_window_arrives_as_a_day_frame_fact() -> None:
    """"I'll sleep today from 00:30 until 8:30" is the frame, typed (#251).

    The schema accepts the kind and the value flows through untouched; the
    kernel's readiness check is what reads it. What is asserted here is the
    plumbing -- not the model's wording.
    """

    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts

    client = _SchemaOutputClient(
        {
            "decision": "provide_facts",
            "facts": [
                {"kind": "day_frame", "value": {"wake": "08:30", "sleep": "00:30"}}
            ],
        }
    )

    intent = await TimeboxingIntentInterpreter(client).interpret(
        "I'll sleep today from 00:30 until 8:30", _capture_snapshot()
    )

    assert isinstance(intent, ProvidePlanningFacts)
    assert [fact.kind for fact in intent.facts] == [FactKind.DAY_FRAME]
    assert intent.facts[0].value == {"wake": "08:30", "sleep": "00:30"}
    assert intent.facts[0].source == "user"


def test_the_schema_tells_the_model_what_a_day_frame_looks_like() -> None:
    """The fact drafts are discriminated on kind, each with its own value shape.

    A ``value: JsonValue`` schema said "anything", and the live model answered
    the right times as a JSON-encoded string 8 of 8 times -- correct judgement,
    unusable shape. The schema is where the shape is enforced, so it is what
    is asserted.
    """

    schema = InterpretedTimeboxTurn.model_json_schema()
    drafts = schema["properties"]["facts"]["items"]
    assert set(drafts["discriminator"]["mapping"]) == {
        "requested_activity",
        "day_frame",
        "elicited_statement",  # Stage 1 decision set, spec 2026-09-04
    }
    frame = schema["$defs"]["DayFrameDraft"]["properties"]
    assert set(frame) == {"wake", "sleep"}
    assert {"type": "null"} in frame["wake"]["anyOf"]
    assert any(
        branch.get("type") == "string" and "HH:MM" in branch.get("description", "")
        for branch in frame["wake"]["anyOf"]
    )


@pytest.mark.asyncio
async def test_the_parsed_turn_stays_json_native_for_autogens_call_log() -> None:
    """AutoGen ``json.dumps`` the parsed completion for its LLMCallEvent.

    A ``datetime.time`` leaf in the draft made ``str(event)`` raise inside
    ``logger.info`` on every live draw. The dump of a validated turn must
    therefore round-trip through ``json.dumps`` untouched.
    """

    turn = InterpretedTimeboxTurn.model_validate(
        {
            "decision": "provide_facts",
            "facts": [
                {"kind": "day_frame", "value": {"wake": "08:30:00Z", "sleep": "0:30"}}
            ],
        }
    )

    dumped = json.loads(json.dumps(turn.model_dump()))

    assert dumped["facts"][0]["value"] == {"wake": "08:30", "sleep": "00:30"}


@pytest.mark.asyncio
async def test_a_day_frame_encoded_as_a_string_is_refused_not_stored() -> None:
    """The exact shape the live model produced before the schema pinned it."""

    client = _SchemaOutputClient(
        {
            "decision": "provide_facts",
            "facts": [
                {"kind": "day_frame", "value": '{"wake": "08:30", "sleep": "00:30"}'}
            ],
        }
    )

    with pytest.raises(ValidationError):
        await TimeboxingIntentInterpreter(client).interpret(
            "I'll sleep today from 00:30 until 8:30", _capture_snapshot()
        )


@pytest.mark.asyncio
async def test_a_one_digit_hour_is_normalised_and_an_unstated_boundary_kept_null() -> None:
    from fateforger.agents.timeboxing.session_contracts import ProvidePlanningFacts

    client = _SchemaOutputClient(
        {
            "decision": "provide_facts",
            "facts": [{"kind": "day_frame", "value": {"wake": "8:30", "sleep": None}}],
        }
    )

    intent = await TimeboxingIntentInterpreter(client).interpret(
        "up at half eight", _capture_snapshot()
    )

    assert isinstance(intent, ProvidePlanningFacts)
    assert intent.facts[0].value == {"wake": "08:30", "sleep": None}


@pytest.mark.asyncio
async def test_the_open_question_is_named_to_the_model_with_what_answers_it() -> None:
    """A reply of "8:30 to half past midnight" only reads as a frame if the model knows what was asked."""

    client = _SchemaOutputClient(
        {
            "decision": "provide_facts",
            "facts": [
                {"kind": "day_frame", "value": {"wake": "08:30", "sleep": "00:30"}}
            ],
        }
    )
    snapshot = _capture_snapshot().model_copy(
        update={
            "pending_blocker": PendingBlocker(
                requirement_id="skeleton.day_frame",
                fact_kind=FactKind.DAY_FRAME,
                options=[],
            )
        }
    )

    await TimeboxingIntentInterpreter(client).interpret("8:30 to half past midnight", snapshot)

    (messages, _schema), = client.calls
    sent = json.loads(messages[-1].content)
    assert sent["open_question"] == {
        "requirement_id": "skeleton.day_frame",
        "answered_by": "day_frame",
    }


@pytest.mark.asyncio
async def test_no_open_question_is_sent_as_none() -> None:
    client = _SchemaOutputClient({"decision": "advance", "facts": []})
    # advance is offered only once the kernel has proposed to close Stage 1.
    # Stage 1 decision set, spec 2026-09-04.
    snapshot = _capture_snapshot().model_copy(update={"stage1": "proposed"})

    await TimeboxingIntentInterpreter(client).interpret("go ahead", snapshot)

    (messages, _schema), = client.calls
    assert json.loads(messages[-1].content)["open_question"] is None


@pytest.mark.asyncio
async def test_a_revision_carrying_a_frame_keeps_the_frame() -> None:
    """One message, two things in it: an instruction and a day-frame fact.

    Live on 2026-09-03 (session 1788429283.534419) the interpreter answered
    "move all the work stuff 1 hour later, I'll sleep until 8:30" with
    ``revise`` plus a ``day_frame`` fact of wake 08:30 -- and the intent
    builder kept only the instruction, so the kernel rebuilt the day against
    the 07:00 wake it had just been told was wrong. What the model extracted
    must reach the kernel whole.
    """

    from fateforger.agents.timeboxing.session_contracts import ReviseArtifact

    snapshot = _committed_snapshot()
    receipt = next(a for a in snapshot.artifacts if a.kind is ArtifactKind.COMMIT_RECEIPT)
    client = _SchemaOutputClient(
        {
            "decision": "revise",
            "facts": [{"kind": "day_frame", "value": {"wake": "08:30", "sleep": None}}],
            "revision_instruction": "move all the work one hour later",
        }
    )

    intent = await TimeboxingIntentInterpreter(client).interpret(
        "Can you move all the work stuff 1 hour later? I'll sleep until 8:30",
        snapshot,
    )

    assert isinstance(intent, ReviseArtifact)
    assert intent.artifact_id == receipt.artifact_id
    assert intent.instruction == "move all the work one hour later"
    assert [fact.kind for fact in intent.facts] == [FactKind.DAY_FRAME]
    assert intent.facts[0].value == {"wake": "08:30", "sleep": None}
    assert intent.facts[0].source == "user"


from fateforger.agents.timeboxing.session_contracts import (
    DenyAssumption,
    FileAssumption,
    PlannerAssumption,
    PlanningFact,
    ProvidePlanningFacts,
    RestoreConstraint,
)
from fateforger.slack_bot.timeboxing_intents import _display_context, _intent_from_interpreted


def _stage1_snapshot(**update) -> PlanningSessionSnapshot:
    return _capture_snapshot().model_copy(
        update={
            "applicable_constraints": [{"uid": "c-gym", "name": "Oats before gym", "necessity": "must", "anchors": []}],
            **update,
        }
    )


def test_stage_one_offers_next_only_after_the_kernel_proposed() -> None:
    _, open_decisions, _ = _display_context(_stage1_snapshot(stage1="open"))
    _, proposed_decisions, _ = _display_context(_stage1_snapshot(stage1="proposed"))
    # Consent already given: the stage stays on the capture branch until a
    # skeleton exists, and Next there re-drives the planner rather than asking
    # for a consent the session already has.
    _, closed_decisions, _ = _display_context(_stage1_snapshot(stage1="closed"))
    assert "advance" not in open_decisions
    assert "advance" in proposed_decisions
    assert "advance" in closed_decisions
    for decisions in (open_decisions, proposed_decisions, closed_decisions):
        # _stage1_snapshot always carries one applicable-constraint row, so
        # steer_not_today is offerable; nothing seeds a pending_blocker or an
        # assumption, so assume and deny must not be offered here -- each is
        # only honourable once the binding could actually satisfy it.
        assert {"provide_facts", "steer_not_today", "back", "cancel"} <= set(decisions)
        assert "assume" not in decisions
        assert "deny" not in decisions
        assert "steer_always" not in decisions  # not honourable until its flow lands
        assert "restore" not in decisions  # nothing is suspended


def test_assume_is_not_offered_against_a_blocker_it_could_never_satisfy() -> None:
    """`assume` files a `PlannerAssumption`, which satisfies a soft cell only.

    Offered against a hard user-owned requirement it would file the assumption,
    leave the blocker standing, and the kernel would ask the same question on
    the next turn -- forever (#251). The set is the forty minted cell ids.
    """

    pending = PendingBlocker(
        requirement_id="skeleton.requested_activity",
        fact_kind=FactKind.REQUESTED_ACTIVITY,
        options=[],
    )
    _, decisions, _ = _display_context(_stage1_snapshot(pending_blocker=pending))
    assert "assume" not in decisions


def test_assume_and_deny_are_offered_once_the_state_they_bind_to_exists() -> None:
    pending = PendingBlocker(requirement_id="elicit.body.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    filed = PlannerAssumption(assumption_id="a1", requirement_id="elicit.body.unclear", value="x", why_needed="y", filed_by="user")
    _, decisions, _ = _display_context(_stage1_snapshot(pending_blocker=pending, assumptions=[filed]))
    assert "assume" in decisions
    assert "deny" in decisions


def test_restore_is_offered_only_while_something_is_suspended() -> None:
    suspend = PlanningFact(
        fact_id="suspend:c-gym", kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    _, decisions, _ = _display_context(_stage1_snapshot(facts=[suspend]))
    assert "restore" in decisions


async def test_restore_names_a_suspended_rule() -> None:
    suspend = PlanningFact(
        fact_id="suspend:c-gym", kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": "c-gym", "reason": "not today"}, source="user",
    )
    client = _SchemaOutputClient({"decision": "restore", "constraint_uid": "c-gym"})
    intent = await TimeboxingIntentInterpreter(client).interpret("put the oats rule back", _stage1_snapshot(facts=[suspend]))
    assert intent == RestoreConstraint(constraint_uid="c-gym")


async def test_not_today_names_a_rule_the_snapshot_holds() -> None:
    client = _SchemaOutputClient({"decision": "steer_not_today", "constraint_uid": "c-gym"})
    intent = await TimeboxingIntentInterpreter(client).interpret("skip the oats thing today", _stage1_snapshot())
    assert isinstance(intent, ProvidePlanningFacts)
    [fact] = intent.facts
    assert fact.kind is FactKind.SUSPENDED_CONSTRAINT
    assert fact.fact_id == "suspend:c-gym"
    assert fact.value == {"uid": "c-gym", "reason": "not today"}


async def test_not_today_for_a_rule_not_on_the_card_is_refused() -> None:
    client = _SchemaOutputClient({"decision": "steer_not_today", "constraint_uid": "c-other"})
    with pytest.raises(ValueError, match="not among"):
        await TimeboxingIntentInterpreter(client).interpret("skip it", _stage1_snapshot())


async def test_assume_files_against_the_open_cell() -> None:
    pending = PendingBlocker(requirement_id="elicit.body.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    client = _SchemaOutputClient({"decision": "assume"})
    intent = await TimeboxingIntentInterpreter(client).interpret("just assume a normal day", _stage1_snapshot(pending_blocker=pending))
    assert isinstance(intent, FileAssumption) and intent.requirement_id == "elicit.body.unclear"


def test_assume_with_nothing_open_is_refused() -> None:
    # assume is not offered when nothing is pending (_display_context), so the
    # interpreter itself would refuse before the binding ever ran. Calling the
    # binding directly bypasses the surface's allowed-decisions gate and
    # exercises its own defence-in-depth refusal.
    interpreted = InterpretedTimeboxTurn(decision="assume")
    with pytest.raises(ValueError, match="no open"):
        _intent_from_interpreted(interpreted, snapshot=_stage1_snapshot(), pending=None)


async def test_deny_names_an_assumption_on_record() -> None:
    filed = PlannerAssumption(assumption_id="a1", requirement_id="elicit.body.unclear", value="x", why_needed="y", filed_by="user")
    client = _SchemaOutputClient({"decision": "deny", "assumption_id": "a1"})
    intent = await TimeboxingIntentInterpreter(client).interpret("no, don't assume that", _stage1_snapshot(assumptions=[filed]))
    assert intent == DenyAssumption(assumption_id="a1")


async def test_an_answer_to_a_probe_is_an_elicited_statement_for_that_cell() -> None:
    pending = PendingBlocker(requirement_id="elicit.fixed.unclear", fact_kind=FactKind.ELICITED_STATEMENT, options=[])
    client = _SchemaOutputClient(
        {"decision": "provide_facts", "facts": [{"kind": "elicited_statement", "value": "dentist at 15:00, fixed"}]}
    )
    intent = await TimeboxingIntentInterpreter(client).interpret("dentist at 15:00, fixed", _stage1_snapshot(pending_blocker=pending))
    [fact] = intent.facts
    assert fact.kind is FactKind.ELICITED_STATEMENT
    assert fact.value == {"cell": "elicit.fixed.unclear", "text": "dentist at 15:00, fixed"}
    assert fact.fact_id.startswith("elicited:elicit.fixed.unclear:")


def test_a_deny_button_press_binds_to_its_assumption() -> None:
    envelope = intent_from_artifact_action(
        {"session_key": "C1:1.0", "expected_revision": 3, "decision": "deny_assumption", "assumption_id": "a1"}
    )
    assert envelope is not None and envelope.intent == DenyAssumption(assumption_id="a1")
    assert intent_from_artifact_action({"session_key": "C1:1.0", "expected_revision": 3, "decision": "deny_assumption"}) is None



def test_a_restore_button_press_binds_to_its_constraint() -> None:
    envelope = intent_from_artifact_action(
        {"session_key": "C1:1.0", "expected_revision": 3, "decision": "restore", "constraint_uid": "c-gym"}
    )
    assert envelope is not None and envelope.intent == RestoreConstraint(constraint_uid="c-gym")
    assert intent_from_artifact_action({"session_key": "C1:1.0", "expected_revision": 3, "decision": "restore"}) is None


async def test_a_malformed_suspension_fact_makes_restore_raise_naming_the_fact() -> None:
    malformed = PlanningFact(
        fact_id="suspend:broken", kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"reason": "not today"}, source="user",
    )
    client = _SchemaOutputClient({"decision": "restore", "constraint_uid": "c-gym"})
    with pytest.raises(ValueError, match="suspend:broken"):
        await TimeboxingIntentInterpreter(client).interpret(
            "put the oats rule back", _stage1_snapshot(facts=[malformed])
        )
