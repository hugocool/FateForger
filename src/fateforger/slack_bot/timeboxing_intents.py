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

from fateforger.agents.timeboxing.elicitation import ALL_CELLS
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactKind,
    AskQuestion,
    BlockerOption,
    CancelSession,
    ChooseBlockerOption,
    ConfirmPlanningDay,
    DayType,
    DenyAssumption,
    FactKind,
    FileAssumption,
    GoBack,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    RestoreConstraint,
    ReviseArtifact,
    StartSession,
    TimeboxIntent,
    elicited_fact_id,
    suspension_fact_id,
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


class ElicitedStatementDraft(_StrictModel):
    """What the user said in Stage 1, in their words. The cell it answers is
    the open question's, bound by the host, never named by the model."""

    kind: Literal["elicited_statement"]
    value: str = Field(min_length=1)


#: One shape per fact kind the interpreter may extract, discriminated on
#: ``kind`` so the schema itself tells the model what a day_frame looks like.
#: With ``value: JsonValue`` the schema said "anything", and on the live model
#: 8 of 8 draws for "I'll sleep today from 00:30 untill 8:30" answered the
#: right times as a JSON-encoded *string* -- correct judgement, unusable shape.
PlanningFactDraft = Annotated[
    RequestedActivityDraft | DayFrameFactDraft | ElicitedStatementDraft,
    Field(discriminator="kind"),
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
        "steer_not_today",
        "restore",
        "assume",
        "deny",
        "question",
        "start",
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
    #: Which rule to set aside for this session, by the uid the card offered.
    #: The host checks it is on the card; an id the model invents is refused.
    constraint_uid: str | None = None
    #: Which assumption to withdraw, by the id the card offered.
    assumption_id: str | None = None


class ArtifactActionMeta(_StrictModel):
    """Every typed review decision this route draws, bound to its session.

    Aliases below (`sk`, `rev`, `d`, `cu`, `aid`, `n`) exist for one reason:
    an overflow option's `value` is capped by Slack at 150 chars, and a real
    session key (a Slack channel and thread timestamp) plus a real constraint
    uid (32 hex chars) or assumption id (a 36-char uuid) leave no room for
    this schema's own field names -- `"expected_revision":` alone is 21
    bytes. `populate_by_name=True` means either spelling decodes, so a
    button's value (still the full field names -- 2000 chars is no constraint)
    and an overflow's value (the aliases, via `_option_value` in
    `timeboxing_cards.py`) both decode through the one path in
    `intent_from_artifact_action`, which reads attributes and never cares
    which spelling arrived on the wire.
    """

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    schema_version: Literal[1] = 1
    session_key: str = Field(min_length=1, alias="sk")
    expected_revision: int = Field(ge=0, alias="rev")
    decision: Literal[
        "advance",
        "approve",
        "revise",
        "back",
        "cancel",
        "choose_option",
        "deny_assumption",
        "restore",
        "steer_not_today",
    ] = Field(alias="d")
    artifact_id: str | None = Field(default=None, min_length=1)
    artifact_revision: int | None = Field(default=None, ge=1)
    artifact_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    revision_instruction: str | None = Field(default=None, min_length=1)
    requirement_id: str | None = Field(default=None, min_length=1)
    option_id: str | None = Field(default=None, min_length=1)
    assumption_id: str | None = Field(default=None, min_length=1, alias="aid")
    constraint_uid: str | None = Field(default=None, min_length=1, alias="cu")
    note: str | None = Field(default=None, min_length=1, alias="n")

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
        if self.decision == "deny_assumption" and self.assumption_id is None:
            raise ValueError("denying an assumption requires its id")
        if self.decision == "restore" and self.constraint_uid is None:
            raise ValueError("restoring a rule requires its uid")
        if self.decision == "steer_not_today" and self.constraint_uid is None:
            raise ValueError("a steer press names the rule it suspends")
        return self


class TimeboxActionEnvelope(_StrictModel):
    """Validated UI action ready for the common session executor boundary."""

    session_key: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    intent: TimeboxIntent


_TIMEBOX_PROMPT_FRAGMENT_BASE = """Extract facts only when the user actually supplies them.
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
When the surface offers steer_not_today, the user is setting one rule from
the card aside for this session; answer with that rule's constraint_uid from
the card, never a name. When it offers restore, the user is putting a rule
they set aside back; answer with that rule's constraint_uid. When it offers assume, the user is telling you to move
on without the answer to the open question; nothing else is needed. When it
offers deny, the user is withdrawing an assumption shown on the card; answer
with its assumption_id. A reply to an open elicit.* question, or anything
the user states about what holds today that is not a request for the day, is
an elicited_statement fact in their words.
"""

QUESTION_PARAGRAPH = """A reply that asks about the day, the plan, the calendar, or what was
decided -- "is it planned?", "did you add the gym?", "what did we settle on
for lunch?", "when is deep work?" -- is question. A reply that supplies a
fact, a correction, or an instruction against the plan is what it was
before. A reply that asks and also supplies a fact is that fact: the fact
changes the day and the question does not.
"""

_TIMEBOX_PROMPT_FRAGMENT = _TIMEBOX_PROMPT_FRAGMENT_BASE + QUESTION_PARAGRAPH


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


#: The forty Stage 1 cell requirement ids, the only blockers `assume` can
#: answer. Built once at import from the catalog the cells are generated from.
_CELL_IDS: frozenset[str] = frozenset(cell.id for cell in ALL_CELLS)


def _display_context(
    snapshot: PlanningSessionSnapshot,
) -> tuple[str, tuple[str, ...], PlanningArtifact | None]:
    pending = _pending_artifact(snapshot)
    if snapshot.planning_day is None and not any(
        artifact.kind is ArtifactKind.PLANNING_DAY for artifact in snapshot.artifacts
    ):
        # Before a day is even proposed there is still something to decide:
        # start the session, ask about the calendar, or walk away. This used
        # to be an unconditional StartSession with no model asked (#318).
        return "no_session", ("start", "question", "cancel"), pending
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
            ("provide_facts", "revise", "question"),
            _latest_artifact(snapshot, ArtifactKind.COMMIT_RECEIPT),
        )
    if snapshot.planning_day is None:
        # Confirming belongs here as much as cancelling does. Without it a
        # session driven by typing cannot get past stage 0 at all, and the two
        # surfaces this project deliberately converged have diverged again.
        return (
            "planning_day",
            ("confirm_planning_day", "cancel", "question"),
            pending,
        )
    if pending is not None and pending.kind is ArtifactKind.SKELETON:
        return (
            "skeleton",
            ("provide_facts", "approve", "revise", "back", "cancel", "question"),
            pending,
        )
    if pending is not None and pending.kind is ArtifactKind.VALIDATED_CANDIDATE:
        return (
            "review_commit",
            ("approve", "revise", "back", "cancel", "question"),
            pending,
        )
    # Choosing is offered exactly while a question with options is open. It is
    # absent everywhere else because there would be no question to answer, and a
    # decision the session cannot honour is one the model can only waste a turn
    # on.
    choose = ("choose_option",) if _offered_options(snapshot) else ()
    if _latest_artifact(snapshot, ArtifactKind.SKELETON) is None:
        # Stage 1. Next is offered only after the kernel proposed to close, and
        # steer_always is absent until its ask-first flow lands: a decision the
        # session cannot honour is one the model can only waste a turn on --
        # the same reason steer_not_today, assume and deny below are each
        # conditioned on the state their binding actually requires.
        # Proposed is the kernel offering to close; closed is consent already
        # given, and the turn re-drives the planner rather than proposing
        # again. Only "open" has nothing for Next to mean.
        consent = ("advance",) if snapshot.stage1 in ("proposed", "closed") else ()
        restore = (
            ("restore",)
            if any(fact.kind is FactKind.SUSPENDED_CONSTRAINT for fact in snapshot.facts)
            else ()
        )
        steer_not_today = (
            ("steer_not_today",) if snapshot.applicable_constraints else ()
        )
        # Only a Stage 1 cell can be assumed past. `FileAssumption` files a
        # `PlannerAssumption`, which satisfies a soft requirement and nothing
        # else, so offering it against a hard user-owned blocker such as
        # `skeleton.requested_activity` would file an assumption, leave the
        # blocker standing and ask the same question again forever (#251).
        # Membership over the forty ids this system minted.
        assume = (
            ("assume",)
            if snapshot.pending_blocker is not None
            and snapshot.pending_blocker.requirement_id in _CELL_IDS
            else ()
        )
        deny = ("deny",) if snapshot.assumptions else ()
        return (
            "capture",
            (
                "provide_facts",
                *steer_not_today,
                *restore,
                *assume,
                *deny,
                *consent,
                *choose,
                "back",
                "cancel",
                "question",
            ),
            None,
        )
    return (
        "refine",
        ("provide_facts", "advance", *choose, "back", "cancel", "question"),
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
            interpreted, snapshot=snapshot, pending=pending, user_text=user_text
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


def _suspension_fact(uid: str, note: str | None) -> PlanningFact:
    """One builder for the suspension fact, so a press and a typed sentence
    file the identical fact at the identical id -- never two shapes drifting."""
    return PlanningFact(
        fact_id=suspension_fact_id(uid),
        kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": uid, "reason": "not today", "note": note},
        source="user",
    )


def _intent_from_interpreted(
    interpreted: InterpretedTimeboxTurn,
    *,
    snapshot: PlanningSessionSnapshot,
    pending: PlanningArtifact | None,
    user_text: str,
) -> TimeboxIntent:
    """Bind one schema decision to trusted host state."""
    if interpreted.decision == "question":
        # The host's copy of the words, verbatim. The schema carries no text
        # field for this decision on purpose: a paraphrase is the model's
        # words reaching the answerer as if the user said them.
        return AskQuestion(question=user_text)
    if interpreted.decision == "start":
        return StartSession()
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
    if interpreted.decision == "steer_not_today":
        offered = {row.get("uid") for row in snapshot.applicable_constraints if isinstance(row, dict)}
        if interpreted.constraint_uid is None or interpreted.constraint_uid not in offered:
            raise ValueError("steer_not_today names a rule not among the card's rows")
        return ProvidePlanningFacts(
            facts=[_suspension_fact(interpreted.constraint_uid, note=None)]
        )
    if interpreted.decision == "restore":
        suspended: set[object] = set()
        for fact in snapshot.facts:
            if fact.kind is not FactKind.SUSPENDED_CONSTRAINT:
                continue
            if not isinstance(fact.value, dict) or "uid" not in fact.value:
                # A malformed suspension is not the same as no suspension --
                # reading it as absent would let a restore through that names
                # nothing real. Mirrors _unsuspended in adaptive_timeboxing.py.
                raise ValueError(
                    f"suspended-constraint fact {fact.fact_id!r} carries no uid"
                )
            suspended.add(fact.value["uid"])
        if interpreted.constraint_uid is None or interpreted.constraint_uid not in suspended:
            raise ValueError("restore names a rule that is not set aside")
        return RestoreConstraint(constraint_uid=interpreted.constraint_uid)
    if interpreted.decision == "assume":
        open_question = snapshot.pending_blocker
        if open_question is None:
            raise ValueError("assume requires an open question; there is no open question")
        return FileAssumption(
            requirement_id=open_question.requirement_id,
            value="assumed by the user",
            why_needed="the user chose to move on without answering",
        )
    if interpreted.decision == "deny":
        known = {a.assumption_id for a in snapshot.assumptions}
        if interpreted.assumption_id is None or interpreted.assumption_id not in known:
            raise ValueError("deny names an assumption not on record")
        return DenyAssumption(assumption_id=interpreted.assumption_id)
    if interpreted.decision == "advance":
        return Advance()
    if interpreted.decision == "back":
        return GoBack()
    if interpreted.decision == "cancel":
        return CancelSession()

    if interpreted.decision == "provide_facts":
        if not interpreted.facts:
            raise ValueError("provide_facts requires at least one typed fact")
        return ProvidePlanningFacts(facts=_typed_facts(interpreted, snapshot))

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
        facts=_typed_facts(interpreted, snapshot),
    )


def _typed_facts(
    interpreted: InterpretedTimeboxTurn, snapshot: PlanningSessionSnapshot
) -> list[PlanningFact]:
    pending = snapshot.pending_blocker
    open_cell = (
        pending.requirement_id
        if pending is not None and pending.fact_kind is FactKind.ELICITED_STATEMENT
        else None
    )
    facts: list[PlanningFact] = []
    for fact in interpreted.facts:
        if isinstance(fact, ElicitedStatementDraft):
            facts.append(
                PlanningFact(
                    fact_id=elicited_fact_id(open_cell),
                    kind=FactKind.ELICITED_STATEMENT,
                    value={"cell": open_cell, "text": fact.value},
                    source="user",
                )
            )
            continue
        facts.append(
            PlanningFact(
                fact_id=str(uuid4()),
                kind=FactKind(fact.kind),
                value=fact.value.as_value() if isinstance(fact.value, DayFrameDraft) else fact.value,
                source="user",
            )
        )
    return facts


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
    elif meta.decision == "deny_assumption":
        intent = DenyAssumption(assumption_id=cast(str, meta.assumption_id))
    elif meta.decision == "restore":
        intent = RestoreConstraint(constraint_uid=cast(str, meta.constraint_uid))
    elif meta.decision == "steer_not_today":
        uid = cast(str, meta.constraint_uid)
        # The same id the typed path files (Phase 1 Task 8), so a press and
        # a sentence cannot suspend one rule twice.
        intent = ProvidePlanningFacts(facts=[_suspension_fact(uid, note=meta.note)])
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
    "ElicitedStatementDraft",
    "InterpretedTimeboxTurn",
    "PlanningFactDraft",
    "RequestedActivityDraft",
    "TimeboxActionEnvelope",
    "TimeboxingIntentInterpreter",
    "intent_from_artifact_action",
    "intent_from_date_action",
]
