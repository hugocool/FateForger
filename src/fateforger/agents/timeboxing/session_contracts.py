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
    #: When the user gets up and when they go to sleep on the planning day, as
    #: ``{"wake": "HH:MM", "sleep": "HH:MM"}`` in the planning timezone. The
    #: user's to state, whether typed today or already on record in constraint
    #: memory -- never the planner's to assume. On 2026-09-02 the planner
    #: assumed it, committed the day, and the user's next message was the
    #: correction the committed session then refused (#251).
    DAY_FRAME = "day_frame"
    #: How a requested activity the planner could not read is to be titled --
    #: the user's pick among the readings the planner proposed. Recorded by a
    #: press, so the value is the option's label and effect (#251).
    ACTIVITY_READING = "activity_reading"
    ORDINARY_PLACEMENT = "ordinary_placement"
    CALENDAR_SNAPSHOT = "calendar_snapshot"
    ACTIVE_CONSTRAINTS = "active_constraints"
    CONCRETE_PLACEMENTS = "concrete_placements"
    #: What the user asked to change about an artifact, in their words. No
    #: requirement is satisfied by it; it is carried so the planner rebuilding
    #: the artifact can read what was wrong with the last one.
    REVISION_INSTRUCTION = "revision_instruction"
    #: The registered kinds of block the day's active rules say must be on the
    #: plan, as ``{"slugs": [...], "by_rule": {slug: {"uid", "name"}}}``.
    #: Filed by the host at candidate time from memory's ``requires_block``
    #: values; read by readiness (open while it lists any slug), by the brief
    #: (which names each one with its rule) and by the submit and acceptance
    #: checks. Never filed when no rule requires a kind.
    REQUIRED_BLOCKS = "required_blocks"
    #: The Stage 1 coverage matrix: one state per cell, plus the anchor
    #: placement it was classified against. Rewritten whole on every fold under
    #: the stable id `coverage:{day}`, so Back, redo and a restart see one state.
    COVERAGE_MATRIX = "coverage_matrix"
    #: What the user said in answer to a probe, or volunteered in Stage 1, as
    #: ``{"cell": <requirement id or null>, "text": <their words>}``. Never
    #: reused as a request: a request is what they want, this is what holds.
    ELICITED_STATEMENT = "elicited_statement"
    #: A rule the user set aside for this session, ``{"uid": ..., "reason": ...}``
    #: under the id `suspend:{uid}`, so a second "not today" is a no-op and a
    #: restore is deleting one fact. The brief drops the rule; the card shows it
    #: as suspended; memory is untouched.
    SUSPENDED_CONSTRAINT = "suspended_constraint"


def coverage_fact_id(day: date_type) -> str:
    return f"coverage:{day.isoformat()}"


def suspension_fact_id(constraint_uid: str) -> str:
    return f"suspend:{constraint_uid}"


def elicited_fact_id(cell_id: str | None) -> str:
    return f"elicited:{cell_id or 'free'}:{uuid4()}"


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
    #: Who supplied the assumption. The planner files one when it must place
    #: something nobody stated; the user files one to force past an open cell.
    #: Both are visible and deniable; only the label differs.
    filed_by: Literal["planner", "user"] = "planner"


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
    #: The active rules the host resolved for this day, written on every
    #: resolve, so the card renders the rows the planner receives, in the
    #: planner's order (#202). Rows are the flat dicts the KG client returns;
    #: nothing here reads their prose. The presence fact stays count-only.
    applicable_constraints: list[dict[str, JsonValue]] = Field(default_factory=list)
    #: How many rules memory holds back for this day type (a vacation day
    #: suspends every working rule). A count because the rows would flood a
    #: card; written by the same resolve that writes the rows.
    suspended_constraint_count: int = Field(default=0, ge=0)
    #: Where Stage 1 stands. `open`: eliciting or not yet evaluated. `proposed`:
    #: the kernel emitted GateMet and is waiting for consent. `closed`: the user
    #: consented, or a Stage 2 fact arrived, and planning may proceed.
    stage1: Literal["open", "proposed", "closed"] = "open"
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


def has_commit_receipt(snapshot: PlanningSessionSnapshot | None) -> bool:
    """Whether anything this session did has reached the calendar.

    The receipt, not the status. `status` says what the session is doing now:
    a revision reopens a committed session (`_reopen`) and it reads `open`
    again, while `_invalidate` keeps the `COMMIT_RECEIPT` precisely because
    history cannot be rebuilt. So the receipt is the only durable answer to
    "has this day been written?", and it is what the kernel and the cards must
    both gate on.

    A receipt is only ever stored for a write the adapter reported as
    committed, but the payload is checked anyway: a store written before that
    guard existed can hold `{"committed": false}`, and a refused commit is not
    a day on the calendar.
    """

    if snapshot is None:
        return False
    return any(
        artifact.kind is ArtifactKind.COMMIT_RECEIPT
        and isinstance(artifact.payload, dict)
        and artifact.payload.get("committed") is True
        for artifact in snapshot.artifacts
    )


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
    #: What the user stated in the same breath as the instruction. "Move the
    #: work an hour later, I'll sleep until 8:30" is one message carrying a
    #: day frame and a revision; a turn takes one intent, so the facts ride
    #: with it and are filed before the redraft reads them.
    facts: list[PlanningFact] = Field(default_factory=list)


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


class FileAssumption(_StrictModel):
    """The user forcing past an open cell: the missing fact supplied as an
    assumption, visible in the card's decided list and deniable there."""

    kind: Literal["file_assumption"] = "file_assumption"
    requirement_id: str = Field(min_length=1)
    value: JsonValue
    why_needed: str = Field(min_length=1)


class DenyAssumption(_StrictModel):
    """Withdraw one assumption, the planner's or the user's, by its minted id.

    The kernel removes it and invalidates what was built on it; the cell it
    answered re-opens. A denial is the user asking to be asked, so re-asking
    that cell is not a violation of ask-once.
    """

    kind: Literal["deny_assumption"] = "deny_assumption"
    assumption_id: str = Field(min_length=1)


class RestoreConstraint(_StrictModel):
    """Undo a "not today": delete the suspension fact for one rule, by uid.

    Restore is deleting one fact; there is no second copy of the rule to
    write back, because the rule never left memory.
    """

    kind: Literal["restore_constraint"] = "restore_constraint"
    constraint_uid: str = Field(min_length=1)


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
        FileAssumption,
        DenyAssumption,
        RestoreConstraint,
        GoBack,
        CancelSession,
    ],
    Field(discriminator="kind"),
]


class CellRef(_StrictModel):
    """One cell of the Stage 1 coverage matrix: a row and a criterion.

    Both halves are keys this system minted (`elicitation.ROWS`,
    `elicitation.CRITERIA`); the id is the requirement the cell is held as.
    """

    row: str = Field(min_length=1)
    criterion: str = Field(min_length=1)

    @property
    def id(self) -> str:
        return f"elicit.{self.row}.{self.criterion}"


class Gate(_StrictModel):
    """What Stage 1 still needs, typed, so a card renders it without prose.

    `open_cells` is empty exactly when the gate is met. `day_label` is the
    day type and weekday the card names ("working Tuesday"). `note` is the only
    prose and may be absent; nothing branches on it.
    """

    open_cells: list[CellRef] = Field(default_factory=list)
    day_label: str = Field(min_length=1)
    note: str | None = None


class AwaitingUser(_StrictModel):
    kind: Literal["awaiting_user"] = "awaiting_user"
    requirement_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    why_needed: str = Field(min_length=1)
    #: Empty means the question is open and takes free text. Non-empty means the
    #: answer set is closed and the renderer may offer it as buttons.
    options: list[BlockerOption] = Field(default_factory=list, max_length=4)
    #: Present only for a Stage 1 probe: the cells still open, the top one
    #: being asked. Absent for every other question the catalog puts.
    gate: Gate | None = None


class GateMet(_StrictModel):
    """Stage 1 proposes to close: nothing is uncovered, and the user decides.

    The renderer offers Next on this outcome and on no other. Consent is the
    user's next message; silence does nothing.
    """

    kind: Literal["gate_met"] = "gate_met"
    gate: Gate


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


class PlannerContinuation(_StrictModel):
    """The planner saying it needs another turn, and why.

    Added because a planner that exhausted its patch-retry budget had no typed
    way to say so. It used a blocker -- the only channel available -- on a
    requirement it owned, and the kernel refused that as `illegal_user_blocker`
    and discarded the turn along with the artifacts and the diagnosis.

    The refusal was right. The gap was that a planner with something true to say
    had no way to say it, which is the same shape as the mis-scoped assumption
    in #236: both reach for the nearest wrong channel.

    `reason` is prose, and stays prose. It is written for the next turn's brief
    so the planner can resume from its own diagnosis instead of rediscovering
    it, and nothing branches on its contents.
    """

    reason: str = Field(min_length=1)


class NeedsAnotherTurn(_StrictModel):
    """Outcome: work was done, it is kept, and the planner wants to continue.

    Deliberately not a `TurnFailed`. Nothing went wrong, the session stays open,
    and whatever artifacts the turn produced are retained -- discarding them is
    what made the old behaviour expensive, since the planner had already worked
    out what remained.
    """

    kind: Literal["needs_another_turn"] = "needs_another_turn"
    reason: str = Field(min_length=1)


class TurnFailed(_StrictModel):
    kind: Literal["turn_failed"] = "turn_failed"
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


TurnOutcome = Annotated[
    Union[
        NeedsAnotherTurn,
        AwaitingUser,
        GateMet,
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


class SkeletonPayload(_StrictModel):
    """What a `skeleton` artifact's payload has to carry to be drawn.

    Loose markdown -- `# anchor` headings and `-` bullets -- plus the reasoning
    that put things where they are. `blocks`, `events` and any other shape a
    planner invents are refused here by name rather than rendered as an empty
    card (#267). Strictness is inherited: no coercion, no extra keys.
    """

    markdown: str = Field(min_length=1)
    reasoning: str = ""


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
    #: Set when the planner cannot finish this turn but has not failed. The
    #: artifacts and assumptions beside it are still applied.
    continuation: PlannerContinuation | None = None


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
    "has_commit_receipt",
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
    "SkeletonPayload",
    "StartSession",
    "TimeboxIntent",
    "TurnFailed",
    "TurnOutcome",
    "UserBlockerDraft",
]
