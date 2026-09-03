"""Typed natural-language and Block Kit adapters for adaptive timeboxing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Annotated, Literal, cast
from uuid import uuid4

from autogen_core.models import ChatCompletionClient
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactKind,
    BlockerOption,
    CancelSession,
    ChooseBlockerOption,
    ConfirmPlanningDay,
    DayType,
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
from fateforger.slack_bot.surface_intents import (
    Clock,
    SurfaceIntentInterpreter,
    SurfaceView,
)
from fateforger.slack_bot.timeboxing_commit import TimeboxCommitMeta


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DayFrameDraft(_StrictModel):
    """The sleep window as the model read it, a boundary left null when unstated."""

    wake: Clock | None
    sleep: Clock | None

    def as_value(self) -> dict[str, str | None]:
        return {"wake": self.wake, "sleep": self.sleep}


class RequestedActivityDraft(_StrictModel):
    kind: Literal["requested_activity"]
    value: str = Field(min_length=1)


class DayFrameFactDraft(_StrictModel):
    kind: Literal["day_frame"]
    value: DayFrameDraft


#: One shape per fact kind the interpreter may extract, discriminated on
#: ``kind`` so the schema itself tells the model what a day_frame looks like.
#: With ``value: JsonValue`` the schema said "anything", and on the live model
#: 8 of 8 draws for "I'll sleep today from 00:30 untill 8:30" answered the
#: right times as a JSON-encoded *string* -- correct judgement, unusable shape.
PlanningFactDraft = Annotated[
    RequestedActivityDraft | DayFrameFactDraft, Field(discriminator="kind")
]


class InterpretedTimeboxTurn(_StrictModel):
    decision: Literal[
        "confirm_planning_day",
        "provide_facts",
        "advance",
        "approve",
        "revise",
        "back",
        "cancel",
    ]
    facts: list[PlanningFactDraft] = Field(default_factory=list)
    revision_instruction: str | None = Field(default=None, min_length=1)
    #: Only the kind of day, never which day. Working and weekend follow from
    #: the weekday and the host already knows them; vacation, holiday and sick
    #: follow from nothing observable, so a chat-driven session has no other way
    #: to say them. Absent means "leave the derived default alone" -- silence is
    #: not a sixth day type, and an override recorded on every confirmation is
    #: one that has stopped meaning anything.
    day_type: DayType | None = None
    #: How many days from the one the host proposed, when the user asks for a
    #: different day. The card offers a date picker and chat had no equivalent,
    #: so "make it Monday" read as agreement with the proposal.
    #:
    #: An offset rather than a date, deliberately. The model is given the
    #: proposed date and its weekday and answers with a distance; the host does
    #: the arithmetic and re-derives the weekday and day type from the result.
    #: A model naming a date directly is the 2026-08-29 incident - a Saturday
    #: read back as a Friday - and no bound catches that, because the wrong
    #: date is perfectly well formed. The bounds here catch the other failure:
    #: a plausible-looking offset that lands a plan a year away.
    day_offset: int | None = Field(default=None, ge=-7, le=14)


class ArtifactActionMeta(_StrictModel):
    schema_version: Literal[1] = 1
    session_key: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    decision: Literal[
        "advance", "approve", "revise", "back", "cancel", "choose_option"
    ]
    artifact_id: str | None = Field(default=None, min_length=1)
    artifact_revision: int | None = Field(default=None, ge=1)
    artifact_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    revision_instruction: str | None = Field(default=None, min_length=1)
    requirement_id: str | None = Field(default=None, min_length=1)
    option_id: str | None = Field(default=None, min_length=1)

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
        # A press names which question and which answer. Half a pair could only
        # be completed by guessing, and a guessed requirement would file the
        # answer against a question the user never saw.
        if self.decision == "choose_option" and (
            self.requirement_id is None or self.option_id is None
        ):
            raise ValueError("an option press requires its question and its option")
        return self


class TimeboxActionEnvelope(_StrictModel):
    """Validated UI action ready for the common session executor boundary."""

    session_key: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    intent: TimeboxIntent


_TIMEBOX_PROMPT_FRAGMENT = """Extract facts only when the user actually supplies them.
Fact kinds you may extract: requested_activity (one per thing the user wants
the day to hold, value a short description in their words) and day_frame
(when they get up and when they go to sleep on the planning day, value
{"wake": "HH:MM", "sleep": "HH:MM"} in 24-hour time, null for a boundary they
did not state). A bedtime or wake time is a day_frame, never an activity.
When open_question is present, the user is answering it: a reply naming
times against a day_frame question is that fact, even without the words
"wake" or "sleep". Never fill in a boundary they did not state.
Set day_type only when the user says what kind of day it is. Leave it out
otherwise: the host derives working and weekend from the weekday and is right
about them, and an override it did not ask for overwrites a fact with a guess.
Set day_offset only when the user asks for a different day from the one in
proposed_day, and give it as a number of days from that day. Leave it out when
they accept the proposal. Never answer with a date; the host owns the calendar.
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


def _offered_options(
    snapshot: PlanningSessionSnapshot,
) -> tuple[BlockerOption, ...]:
    """What the session is currently offering against its open question.

    The pending record is the authority, not the card: it is what the kernel
    checks a choice against, and a card the user scrolled back to may be
    offering something this session has already moved past.
    """

    pending = snapshot.pending_blocker
    return () if pending is None else tuple(pending.options)


def _open_question(snapshot: PlanningSessionSnapshot) -> dict[str, str] | None:
    pending = snapshot.pending_blocker
    if pending is None:
        return None
    return {
        "requirement_id": pending.requirement_id,
        "answered_by": pending.fact_kind.value,
    }


def _display_context(
    snapshot: PlanningSessionSnapshot,
) -> tuple[str, tuple[str, ...], PlanningArtifact | None]:
    pending = _pending_artifact(snapshot)
    if snapshot.status == "cancelled":
        return "cancelled", (), pending
    if snapshot.status == "committed":
        # The day is on the calendar and the user is still talking, which on
        # 2026-09-02 meant "move the work two hours later, I sleep 00:30-08:30"
        # eighty seconds after the commit. The session key is the thread, so a
        # stage that offers nothing here is a thread that is dead for good.
        # What is offered is what changes the day: facts, or an instruction
        # against the receipt. Not cancel -- the calendar is written -- and
        # not approve, which has nothing left to approve.
        return (
            "committed",
            ("provide_facts", "revise"),
            _latest_artifact(snapshot, ArtifactKind.COMMIT_RECEIPT),
        )
    if snapshot.planning_day is None:
        # Confirming belongs here as much as cancelling does. Without it a
        # session driven by typing cannot get past stage 0 at all, and the two
        # surfaces this project deliberately converged have diverged again.
        return "planning_day", ("confirm_planning_day", "cancel"), pending
    if pending is not None and pending.kind is ArtifactKind.SKELETON:
        return (
            "skeleton",
            ("provide_facts", "approve", "revise", "back", "cancel"),
            pending,
        )
    if pending is not None and pending.kind is ArtifactKind.VALIDATED_CANDIDATE:
        return "review_commit", ("approve", "revise", "back", "cancel"), pending
    # Choosing is offered exactly while a question with options is open. It is
    # absent everywhere else because there would be no question to answer, and a
    # decision the session cannot honour is one the model can only waste a turn
    # on.
    choose = ("choose_option",) if _offered_options(snapshot) else ()
    if _latest_artifact(snapshot, ArtifactKind.SKELETON) is None:
        return (
            "capture",
            ("provide_facts", "advance", *choose, "back", "cancel"),
            None,
        )
    return (
        "refine",
        ("provide_facts", "advance", *choose, "back", "cancel"),
        None,
    )


class TimeboxingIntentInterpreter:
    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client
        self._core = SurfaceIntentInterpreter(model_client)

    async def interpret(
        self, user_text: str, snapshot: PlanningSessionSnapshot
    ) -> TimeboxIntent:
        display_stage, allowed_decisions, pending = _display_context(snapshot)
        if not allowed_decisions:
            raise ValueError("the planning session does not accept another intent")
        view = SurfaceView(
            surface_kind="timebox_session",
            display_state=display_stage,
            allowed_decisions=tuple(allowed_decisions),
            offered_options=_offered_options(snapshot),
            # The question this turn may be answering, and the kind of fact
            # that answers it (#251).
            open_question=_open_question(snapshot),
            context={
                # Timeboxing's own prompt key names, kept byte-identical for
                # callers and tests that assert on them.
                "display_stage": display_stage,
                "pending_artifact_kind": pending.kind.value if pending else None,
                # What day_offset is measured from.
                "proposed_day": _proposed_day_context(pending),
            },
        )
        interpreted = await self._core.interpret(
            view=view,
            user_text=user_text,
            schema=InterpretedTimeboxTurn,
            prompt_fragment=_TIMEBOX_PROMPT_FRAGMENT,
            attribution=(
                "timebox_intent_interpreter",
                "timebox_intent",
                snapshot.session_key,
            ),
        )
        return _intent_from_interpreted(
            interpreted, snapshot=snapshot, pending=pending
        )


def _proposed_planning_day(artifact: PlanningArtifact) -> PlanningDay:
    """Read back the day the host proposed, which is the only date on offer.

    The card and the chat reply confirm the same artifact, so both take the
    date from it. Letting the model name a date instead is the 2026-08-29
    incident exactly: a session that read a working-looking day and decided it
    must be a Friday.
    """

    return PlanningDay.model_validate_json(
        json.dumps(artifact.payload, ensure_ascii=False)
    )


def _proposed_day_context(artifact: PlanningArtifact | None) -> dict[str, str] | None:
    """The day an offset is measured from, named so the model can count."""

    if artifact is None or artifact.kind is not ArtifactKind.PLANNING_DAY:
        return None
    proposal = _proposed_planning_day(artifact)
    return {
        "date": proposal.date.isoformat(),
        "weekday": proposal.date.strftime("%A"),
        "day_type": proposal.day_type.value,
    }


def _intent_from_interpreted(
    interpreted: InterpretedTimeboxTurn,
    *,
    snapshot: PlanningSessionSnapshot,
    pending: PlanningArtifact | None,
) -> TimeboxIntent:
    """Bind one schema decision to trusted host state."""
    if interpreted.decision == "confirm_planning_day":
        if pending is None or pending.kind is not ArtifactKind.PLANNING_DAY:
            raise ValueError("confirm_planning_day requires a proposed planning day")
        proposal = _proposed_planning_day(pending)
        # Same lock the button takes, from the same host-derived date. The
        # basis travels with the day type because `PlanningDay` refuses a
        # `calendar` basis that disagrees with the weekday -- so an override
        # that forgot to say it was one would raise rather than lie.
        # The host does the date arithmetic and re-derives the weekday and day
        # type from the result, so a shifted day is as host-owned as the
        # proposal was.
        target_date = proposal.date + timedelta(days=interpreted.day_offset or 0)
        return ConfirmPlanningDay(
            planning_day=PlanningDay.lock_default(
                value=target_date,
                timezone=proposal.timezone,
                lock_revision=snapshot.revision + 1,
                day_type=interpreted.day_type,
            )
        )
    if interpreted.decision == "choose_option":
        pending_blocker = snapshot.pending_blocker
        option_id = getattr(interpreted, "option_id", None)
        if pending_blocker is None or option_id is None:
            raise ValueError("choose_option requires an open question with options")
        # Which question is host state, exactly as artifact identity is. The
        # model says which of the offered answers was meant and nothing else,
        # and the kernel still checks the pair against the record it wrote.
        return ChooseBlockerOption(
            requirement_id=pending_blocker.requirement_id, option_id=option_id
        )
    if interpreted.decision == "advance":
        return Advance()
    if interpreted.decision == "back":
        return GoBack()
    if interpreted.decision == "cancel":
        return CancelSession()

    if interpreted.decision == "provide_facts":
        if not interpreted.facts:
            raise ValueError("provide_facts requires at least one typed fact")
        return ProvidePlanningFacts(facts=_typed_facts(interpreted))

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
    # The facts the model read out of the same message ride with the
    # instruction. Dropping them here was how "move the work an hour later,
    # I'll sleep until 8:30" redrafted the day against the old wake time.
    return ReviseArtifact(
        artifact_id=pending.artifact_id,
        artifact_revision=pending.revision,
        artifact_digest=pending.digest,
        instruction=interpreted.revision_instruction,
        facts=_typed_facts(interpreted),
    )


def _typed_facts(interpreted: InterpretedTimeboxTurn) -> list[PlanningFact]:
    return [
        PlanningFact(
            fact_id=str(uuid4()),
            kind=FactKind(fact.kind),
            value=fact.value.as_value()
            if isinstance(fact.value, DayFrameDraft)
            else fact.value,
            source="user",
        )
        for fact in interpreted.facts
    ]


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
    elif meta.decision == "choose_option":
        # Carried straight through. Both values are identifiers the host minted,
        # and the kernel checks them against the question it is actually
        # holding -- nothing here treats a button value as already trusted.
        intent = ChooseBlockerOption(
            requirement_id=cast(str, meta.requirement_id),
            option_id=cast(str, meta.option_id),
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
    "Clock",
    "DayFrameDraft",
    "DayFrameFactDraft",
    "InterpretedTimeboxTurn",
    "PlanningFactDraft",
    "RequestedActivityDraft",
    "TimeboxActionEnvelope",
    "TimeboxingIntentInterpreter",
    "intent_from_artifact_action",
    "intent_from_date_action",
]
