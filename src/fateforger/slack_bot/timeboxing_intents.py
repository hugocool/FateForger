"""Typed natural-language and Block Kit adapters for adaptive timeboxing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Literal, cast
from uuid import uuid4

from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactKind,
    CancelSession,
    ConfirmPlanningDay,
    FactKind,
    GoBack,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    ReviseArtifact,
    TimeboxIntent,
)
from fateforger.slack_bot.timeboxing_commit import TimeboxCommitMeta


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanningFactDraft(_StrictModel):
    kind: FactKind
    value: JsonValue


class InterpretedTimeboxTurn(_StrictModel):
    decision: Literal[
        "provide_facts", "advance", "approve", "revise", "back", "cancel"
    ]
    facts: list[PlanningFactDraft] = Field(default_factory=list)
    revision_instruction: str | None = Field(default=None, min_length=1)


class ArtifactActionMeta(_StrictModel):
    schema_version: Literal[1] = 1
    session_key: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    decision: Literal["advance", "approve", "revise", "back", "cancel"]
    artifact_id: str | None = Field(default=None, min_length=1)
    artifact_revision: int | None = Field(default=None, ge=1)
    artifact_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    revision_instruction: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def artifact_decisions_have_exact_identity(self) -> ArtifactActionMeta:
        if self.decision in ("approve", "revise") and (
            self.artifact_id is None
            or self.artifact_revision is None
            or self.artifact_digest is None
        ):
            raise ValueError("artifact decisions require exact artifact identity")
        if self.decision == "revise" and self.revision_instruction is None:
            raise ValueError("revision decisions require an instruction")
        return self


class TimeboxActionEnvelope(_StrictModel):
    """Validated UI action ready for the common session executor boundary."""

    session_key: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    intent: TimeboxIntent


_SYSTEM_PROMPT = """You interpret one adaptive timeboxing user turn.
Return only the requested InterpretedTimeboxTurn schema.
Choose only a decision listed in allowed_decisions.
Extract facts only when the user actually supplies them.
Never invent artifact identifiers, revisions, or digests; the host owns identity.
"""


def _is_approved(
    snapshot: PlanningSessionSnapshot, artifact: PlanningArtifact
) -> bool:
    return any(
        approval.artifact_id == artifact.artifact_id
        and approval.artifact_revision == artifact.revision
        and approval.artifact_digest == artifact.digest
        for approval in snapshot.approvals
    )


def _latest_artifact(
    snapshot: PlanningSessionSnapshot, kind: ArtifactKind
) -> PlanningArtifact | None:
    matching = [artifact for artifact in snapshot.artifacts if artifact.kind is kind]
    return max(matching, key=lambda artifact: artifact.revision, default=None)


def _pending_artifact(
    snapshot: PlanningSessionSnapshot,
) -> PlanningArtifact | None:
    if snapshot.planning_day is None:
        return _latest_artifact(snapshot, ArtifactKind.PLANNING_DAY)
    for kind in (ArtifactKind.SKELETON, ArtifactKind.VALIDATED_CANDIDATE):
        artifact = _latest_artifact(snapshot, kind)
        if artifact is not None and not _is_approved(snapshot, artifact):
            return artifact
    return None


def _display_context(
    snapshot: PlanningSessionSnapshot,
) -> tuple[str, tuple[str, ...], PlanningArtifact | None]:
    pending = _pending_artifact(snapshot)
    if snapshot.status == "cancelled":
        return "cancelled", (), pending
    if snapshot.status == "committed":
        return "committed", (), pending
    if snapshot.planning_day is None:
        return "planning_day", ("cancel",), pending
    if pending is not None and pending.kind is ArtifactKind.SKELETON:
        return (
            "skeleton",
            ("provide_facts", "approve", "revise", "back", "cancel"),
            pending,
        )
    if pending is not None and pending.kind is ArtifactKind.VALIDATED_CANDIDATE:
        return "review_commit", ("approve", "revise", "back", "cancel"), pending
    if _latest_artifact(snapshot, ArtifactKind.SKELETON) is None:
        return "capture", ("provide_facts", "advance", "back", "cancel"), None
    return "refine", ("provide_facts", "advance", "back", "cancel"), None


class TimeboxingIntentInterpreter:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def interpret(
        self, user_text: str, snapshot: PlanningSessionSnapshot
    ) -> TimeboxIntent:
        display_stage, allowed_decisions, pending = _display_context(snapshot)
        if not allowed_decisions:
            raise ValueError("the planning session does not accept another intent")
        prompt = json.dumps(
            {
                "display_stage": display_stage,
                "allowed_decisions": list(allowed_decisions),
                "pending_artifact_kind": pending.kind.value if pending else None,
                "user_text": user_text,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        result = await self.model_client.create(
            [
                SystemMessage(content=_SYSTEM_PROMPT),
                UserMessage(content=prompt, source="user"),
            ],
            json_output=InterpretedTimeboxTurn,
        )
        content = getattr(result, "content", None)
        if not isinstance(content, str):
            raise ValueError("intent model returned no schema-bound JSON content")
        interpreted = InterpretedTimeboxTurn.model_validate_json(content)
        if interpreted.decision not in allowed_decisions:
            raise ValueError(
                f"decision {interpreted.decision!r} is not allowed in {display_stage}"
            )
        return _intent_from_interpreted(interpreted, pending=pending)


def _intent_from_interpreted(
    interpreted: InterpretedTimeboxTurn,
    *,
    pending: PlanningArtifact | None,
) -> TimeboxIntent:
    """Bind one schema decision to trusted host state."""
    if interpreted.decision == "advance":
        return Advance()
    if interpreted.decision == "back":
        return GoBack()
    if interpreted.decision == "cancel":
        return CancelSession()

    if interpreted.decision == "provide_facts":
        if not interpreted.facts:
            raise ValueError("provide_facts requires at least one typed fact")
        return ProvidePlanningFacts(
            facts=[
                PlanningFact(
                    fact_id=str(uuid4()),
                    kind=fact.kind,
                    value=fact.value,
                    source="user",
                )
                for fact in interpreted.facts
            ]
        )

    if pending is None:
        raise ValueError(f"{interpreted.decision} requires a pending artifact")
    if interpreted.decision == "approve":
        return ApproveArtifact(
            artifact_id=pending.artifact_id,
            artifact_revision=pending.revision,
            artifact_digest=pending.digest,
        )

    if interpreted.revision_instruction is None:
        raise ValueError("revise requires a revision instruction")
    return ReviseArtifact(
        artifact_id=pending.artifact_id,
        artifact_revision=pending.revision,
        artifact_digest=pending.digest,
        instruction=interpreted.revision_instruction,
    )


def intent_from_artifact_action(
    action: ArtifactActionMeta | str | Mapping[str, object],
) -> TimeboxActionEnvelope | None:
    try:
        if isinstance(action, ArtifactActionMeta):
            meta = action
        elif isinstance(action, str):
            meta = ArtifactActionMeta.model_validate_json(action)
        else:
            meta = ArtifactActionMeta.model_validate(action)
    except (TypeError, ValueError, ValidationError):
        return None

    if meta.decision == "advance":
        intent: TimeboxIntent = Advance()
    elif meta.decision == "approve":
        intent = ApproveArtifact(
            artifact_id=cast(str, meta.artifact_id),
            artifact_revision=cast(int, meta.artifact_revision),
            artifact_digest=cast(str, meta.artifact_digest),
        )
    elif meta.decision == "revise":
        intent = ReviseArtifact(
            artifact_id=cast(str, meta.artifact_id),
            artifact_revision=cast(int, meta.artifact_revision),
            artifact_digest=cast(str, meta.artifact_digest),
            instruction=cast(str, meta.revision_instruction),
        )
    elif meta.decision == "back":
        intent = GoBack()
    else:
        intent = CancelSession()
    return TimeboxActionEnvelope(
        session_key=meta.session_key,
        expected_revision=meta.expected_revision,
        intent=intent,
    )


def intent_from_date_action(value: str) -> TimeboxActionEnvelope | None:
    """Bind one date-card press to a typed planning day.

    A day type present in the metadata came from a button the user pressed, so
    `lock_default` records it as a `user_override`. Absent, the weekday decides.
    Passing an override without the basis would trip `PlanningDay`'s own
    validator, which is the point: a calendar cannot claim a vacation.
    """
    meta = TimeboxCommitMeta.from_value(value)
    if meta is None:
        return None
    return TimeboxActionEnvelope(
        session_key=meta.session_key,
        expected_revision=meta.expected_revision,
        intent=ConfirmPlanningDay(
            planning_day=PlanningDay.lock_default(
                value=date.fromisoformat(meta.date),
                timezone=meta.tz,
                lock_revision=meta.expected_revision + 1,
                day_type=meta.day_type,
            )
        ),
    )


__all__ = [
    "ArtifactActionMeta",
    "InterpretedTimeboxTurn",
    "PlanningFactDraft",
    "TimeboxActionEnvelope",
    "TimeboxingIntentInterpreter",
    "intent_from_artifact_action",
    "intent_from_date_action",
]
