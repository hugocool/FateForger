from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

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
    ConfirmPlanningDay,
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
