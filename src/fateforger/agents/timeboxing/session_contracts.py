"""Typed, serializable contracts for artifact-led planning sessions."""

from __future__ import annotations

import hashlib
import json
from datetime import date as date_type
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class _StrictModel(BaseModel):
    """Reject coercion and undeclared transport fields at this boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)


class CandidateNotApplied(RuntimeError):
    """A validated candidate was drafted without a tmbx patch behind it.

    The host attaches the commit basis by watching the ``plan_apply`` the
    planner made, never by asking the model for one -- so a candidate drafted
    without applying anything has nothing to commit. Raised at the turn that
    produced it, because the alternative is what used to happen: the candidate
    is stored, rendered, approved, and refused three turns later as
    ``malformed_input`` on ``plan_commit({}, {})``.

    Lives here rather than beside the planner because both the kernel that
    answers it and the adapter that raises it import this module, and neither
    imports the other.
    """


class ArtifactKind(StrEnum):
    """Stable artifact vocabulary for a planning session."""

    PLANNING_DAY = "planning_day"
    DAY_FRAME = "day_frame"
    CAPTURED_INPUTS = "captured_inputs"
    PLANNING_BRIEF = "planning_brief"
    SKELETON = "skeleton"
    VALIDATED_CANDIDATE = "validated_candidate"
    COMMIT_RECEIPT = "commit_receipt"


class DayType(StrEnum):
    """Host-locked planning-day classifications."""

    WORKING = "working"
    WEEKEND = "weekend"
    VACATION = "vacation"
    HOLIDAY = "holiday"
    SICK = "sick"


class FactKind(StrEnum):
    """Core planning facts consumed by the initial readiness policy."""

    REQUESTED_ACTIVITY = "requested_activity"
    ORDINARY_PLACEMENT = "ordinary_placement"
    CALENDAR_SNAPSHOT = "calendar_snapshot"
    ACTIVE_CONSTRAINTS = "active_constraints"
    CONCRETE_PLACEMENTS = "concrete_placements"


class PlanningDay(_StrictModel):
    """The host-derived date classification locked for a planning session."""

    date: date_type
    timezone: str = Field(min_length=1)
    iso_weekday: int = Field(ge=1, le=7)
    day_type: DayType
    classification_basis: Literal["calendar", "user_override"]
    lock_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def weekday_matches_date(self) -> PlanningDay:
        if self.iso_weekday != self.date.isoweekday():
            raise ValueError("iso_weekday must match date.isoweekday()")
        expected_day_type = (
            DayType.WEEKEND
            if self.iso_weekday in (6, 7)
            else DayType.WORKING
        )
        if (
            self.classification_basis == "calendar"
            and self.day_type is not expected_day_type
        ):
            raise ValueError(
                "calendar day_type must match the weekday-derived classification"
            )
        return self

    @classmethod
    def lock_default(
        cls,
        *,
        value: date_type,
        timezone: str,
        lock_revision: int,
        day_type: DayType | None = None,
    ) -> PlanningDay:
        """Lock a host date, allowing only a typed day-type override."""

        iso_weekday = value.isoweekday()
        default_day_type = (
            DayType.WEEKEND if iso_weekday in (6, 7) else DayType.WORKING
        )
        return cls(
            date=value,
            timezone=timezone,
            iso_weekday=iso_weekday,
            day_type=day_type or default_day_type,
            classification_basis=(
                "user_override" if day_type is not None else "calendar"
            ),
            lock_revision=lock_revision,
        )


class PlanningFact(_StrictModel):
    """A planning fact with durable provenance."""

    fact_id: str = Field(min_length=1)
    kind: FactKind
    value: JsonValue
    source: Literal["user", "calendar", "constraint_memory", "system"]
    source_interaction_id: str | None = None


class PlannerAssumption(_StrictModel):
    """A planner-owned decision recorded with its invalidation inputs."""

    assumption_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)
    value: JsonValue
    why_needed: str = Field(min_length=1)
    invalidated_by: list[str] = Field(default_factory=list)


def _canonical_digest(
    *,
    kind: ArtifactKind,
    revision: int,
    payload: JsonValue,
    dependency_revisions: dict[str, int],
) -> str:
    canonical = json.dumps(
        {
            "dependency_revisions": dependency_revisions,
            "kind": kind.value,
            "payload": payload,
            "revision": revision,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PlanningArtifact(_StrictModel):
    """A versioned planning artifact whose digest binds its material content."""

    artifact_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    kind: ArtifactKind
    revision: int = Field(ge=1)
    payload: JsonValue
    dependency_revisions: dict[str, int] = Field(default_factory=dict)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def digest_matches_material_content(self) -> PlanningArtifact:
        expected_digest = _canonical_digest(
            kind=self.kind,
            revision=self.revision,
            payload=self.payload,
            dependency_revisions=self.dependency_revisions,
        )
        if self.digest != expected_digest:
            raise ValueError("digest must match the canonical artifact content")
        return self

    @classmethod
    def create(
        cls,
        *,
        kind: ArtifactKind,
        revision: int,
        payload: JsonValue,
        dependency_revisions: dict[str, int],
        artifact_id: str | None = None,
    ) -> PlanningArtifact:
        """Create an artifact with its canonical SHA-256 digest."""

        digest = _canonical_digest(
            kind=kind,
            revision=revision,
            payload=payload,
            dependency_revisions=dependency_revisions,
        )
        fields: dict[str, object] = {
            "kind": kind,
            "revision": revision,
            "payload": payload,
            "dependency_revisions": dependency_revisions,
            "digest": digest,
        }
        if artifact_id is not None:
            fields["artifact_id"] = artifact_id
        return cls.model_validate(fields)


class ArtifactApproval(_StrictModel):
    """An approval explicitly bound to one exact artifact representation."""

    artifact_id: str = Field(min_length=1)
    artifact_revision: int = Field(ge=1)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_user_id: str = Field(min_length=1)
    session_revision: int = Field(ge=0)


class HandledInteraction(_StrictModel):
    """Compact idempotency record retained with the session snapshot."""

    interaction_id: str = Field(min_length=1)
    outcome_kind: str = Field(min_length=1)
    session_revision: int = Field(ge=0)


class BlockerOption(_StrictModel):
    """One concrete alternative offered against an open user decision.

    ``option_id`` is an identifier the host minted and the planner never sees as
    an argument. An id the model chooses is one it could point at a different
    choice than the user read, and the whole value of a button over a text box
    is that the answer arrives already typed and needs no interpretation.
    """

    option_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    effect: str = Field(min_length=1)


class PendingBlocker(_StrictModel):
    """The open user question a session is holding, and what it offered.

    Kept on the snapshot rather than recomputed, because the press arrives a
    turn later and only the host knows what was actually on screen. Checking a
    press against this record is the same shape as checking an approval against
    an exact artifact digest: both are claims about a thing the user saw.

    ``fact_kind`` is captured when the question is put, not looked up when it is
    answered. The requirement catalog is the authority on what would satisfy a
    requirement, and asking it again at press time would let a catalog edit
    change the meaning of a button already rendered.
    """

    requirement_id: str = Field(min_length=1)
    fact_kind: FactKind
    options: list[BlockerOption] = Field(default_factory=list, max_length=4)


class PlanningSessionSnapshot(_StrictModel):
    """Persistent state for a planning session; stage is intentionally absent."""

    schema_version: Literal[1] = 1
    session_key: str = Field(min_length=1)
    revision: int = Field(ge=0)
    owner_user_id: str = Field(min_length=1)
    planning_day: PlanningDay | None = None
    facts: list[PlanningFact] = Field(default_factory=list)
    assumptions: list[PlannerAssumption] = Field(default_factory=list)
    artifacts: list[PlanningArtifact] = Field(default_factory=list)
    approvals: list[ArtifactApproval] = Field(default_factory=list)
    handled_interactions: list[HandledInteraction] = Field(default_factory=list)
    #: The question this session last put to the user, while it is still open.
    #: Absent means no press can be honoured, which is the correct default: a
    #: button pointed at a question nobody is holding any more must not answer
    #: the one that replaced it.
    pending_blocker: PendingBlocker | None = None
    status: Literal["open", "committed", "cancelled"] = "open"

    @model_validator(mode="after")
    def fact_ids_are_unique(self) -> PlanningSessionSnapshot:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_id values must be unique within a session")
        return self

    @classmethod
    def new(cls, *, session_key: str, owner_user_id: str) -> PlanningSessionSnapshot:
        """Create an empty, open planning session."""

        return cls(session_key=session_key, revision=0, owner_user_id=owner_user_id)


class StartSession(_StrictModel):
    kind: Literal["start_session"] = "start_session"


class ConfirmPlanningDay(_StrictModel):
    kind: Literal["confirm_planning_day"] = "confirm_planning_day"
    planning_day: PlanningDay


class ProvidePlanningFacts(_StrictModel):
    kind: Literal["provide_planning_facts"] = "provide_planning_facts"
    facts: list[PlanningFact] = Field(min_length=1)

    @model_validator(mode="after")
    def fact_ids_are_unique(self) -> ProvidePlanningFacts:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_id values must be unique within a fact intent")
        return self


class Advance(_StrictModel):
    kind: Literal["advance"] = "advance"


class ReviseArtifact(_StrictModel):
    kind: Literal["revise_artifact"] = "revise_artifact"
    artifact_id: str = Field(min_length=1)
    artifact_revision: int = Field(ge=1)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruction: str = Field(min_length=1)


class ApproveArtifact(_StrictModel):
    kind: Literal["approve_artifact"] = "approve_artifact"
    artifact_id: str = Field(min_length=1)
    artifact_revision: int = Field(ge=1)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChooseBlockerOption(_StrictModel):
    """One press against one option that was offered for one open question.

    Both fields are identifiers this system minted, so applying a press decides
    nothing about what the user meant -- the meaning was fixed when the option
    was written, and the press only names which one.
    """

    kind: Literal["choose_blocker_option"] = "choose_blocker_option"
    requirement_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)


class GoBack(_StrictModel):
    kind: Literal["go_back"] = "go_back"


class CancelSession(_StrictModel):
    kind: Literal["cancel_session"] = "cancel_session"


TimeboxIntent = Annotated[
    Union[
        StartSession,
        ConfirmPlanningDay,
        ProvidePlanningFacts,
        Advance,
        ReviseArtifact,
        ApproveArtifact,
        ChooseBlockerOption,
        GoBack,
        CancelSession,
    ],
    Field(discriminator="kind"),
]


class AwaitingUser(_StrictModel):
    kind: Literal["awaiting_user"] = "awaiting_user"
    requirement_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    why_needed: str = Field(min_length=1)
    #: Empty means the question is open and takes free text. Non-empty means the
    #: answer set is closed and the renderer may offer it as buttons.
    options: list[BlockerOption] = Field(default_factory=list, max_length=4)


class ArtifactReady(_StrictModel):
    kind: Literal["artifact_ready"] = "artifact_ready"
    artifact: PlanningArtifact


class AwaitingApproval(_StrictModel):
    kind: Literal["awaiting_approval"] = "awaiting_approval"
    artifact: PlanningArtifact


class Committed(_StrictModel):
    kind: Literal["committed"] = "committed"
    receipt: PlanningArtifact


class Cancelled(_StrictModel):
    kind: Literal["cancelled"] = "cancelled"


class TurnFailed(_StrictModel):
    kind: Literal["turn_failed"] = "turn_failed"
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


TurnOutcome = Annotated[
    Union[
        AwaitingUser,
        ArtifactReady,
        AwaitingApproval,
        Committed,
        Cancelled,
        TurnFailed,
    ],
    Field(discriminator="kind"),
]


class ArtifactSnapshot(_StrictModel):
    """Planner-safe representation of a current artifact."""

    artifact_id: str = Field(min_length=1)
    kind: ArtifactKind
    revision: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: JsonValue


class PlanningBrief(_StrictModel):
    """Complete host-owned context supplied to one planner invocation."""

    session_key: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    observed_at: datetime
    locked_day: PlanningDay
    facts: list[PlanningFact]
    assumptions: list[PlannerAssumption]
    current_artifacts: list[ArtifactSnapshot]
    approvals: list[ArtifactApproval]
    applicable_constraints: JsonValue
    calendar_snapshot: JsonValue
    target_artifact: ArtifactKind
    readiness: JsonValue
    allowed_outputs: set[ArtifactKind]


class ArtifactDraft(_StrictModel):
    """A proposed artifact update emitted by the planner."""

    kind: ArtifactKind
    payload: JsonValue
    dependency_revisions: dict[str, int] = Field(default_factory=dict)


class PlannerAssumptionDraft(_StrictModel):
    """A planner-proposed assumption awaiting kernel validation."""

    requirement_id: str = Field(min_length=1)
    value: JsonValue
    why_needed: str = Field(min_length=1)
    invalidated_by: list[str] = Field(default_factory=list)


class UserBlockerDraft(_StrictModel):
    """A specific downstream requirement that needs a user decision."""

    requirement_id: str = Field(min_length=1)
    why_needed: str = Field(min_length=1)
    #: Concrete alternatives, only where the answer set is genuinely closed.
    #: Empty is the ordinary case and stays legal: a planner that offered four
    #: guesses at an open question would hide the fifth answer the user had,
    #: which is exactly the failure buttons are supposed to prevent.
    options: list[BlockerOption] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def option_ids_are_unique(self) -> UserBlockerDraft:
        option_ids = [option.option_id for option in self.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("option_id values must be unique within one blocker")
        return self


class PlanningResult(_StrictModel):
    """Schema-bound output from the planner adapter."""

    artifact_updates: list[ArtifactDraft] = Field(default_factory=list)
    assumptions: list[PlannerAssumptionDraft] = Field(default_factory=list)
    blockers: list[UserBlockerDraft] = Field(default_factory=list)


__all__ = [
    "Advance",
    "ApproveArtifact",
    "ArtifactApproval",
    "ArtifactDraft",
    "ArtifactKind",
    "ArtifactReady",
    "ArtifactSnapshot",
    "AwaitingApproval",
    "AwaitingUser",
    "BlockerOption",
    "Cancelled",
    "CancelSession",
    "ChooseBlockerOption",
    "Committed",
    "ConfirmPlanningDay",
    "DayType",
    "FactKind",
    "GoBack",
    "HandledInteraction",
    "PendingBlocker",
    "PlannerAssumption",
    "PlannerAssumptionDraft",
    "PlanningArtifact",
    "PlanningBrief",
    "PlanningDay",
    "PlanningFact",
    "PlanningResult",
    "PlanningSessionSnapshot",
    "ProvidePlanningFacts",
    "ReviseArtifact",
    "StartSession",
    "TimeboxIntent",
    "TurnFailed",
    "TurnOutcome",
    "UserBlockerDraft",
]
