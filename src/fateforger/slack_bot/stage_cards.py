# src/fateforger/slack_bot/stage_cards.py
"""What one timeboxing card says, as a typed value with no Slack in it.

The kernel produces an outcome; this module turns it into a `StageCard`: which
of the five stages it is, what context fed it, what has been decided, what is
being asked, and which controls it offers. One renderer draws every card from
this, and a receipt is the same card with its controls removed -- so the
message the user reads after moving on is the message they actually acted on.

Stage is derived from the outcome, not from an artifact the kernel does not
mint: stages 1-2 are the planning-day approval and the two user-owned
questions, 3-5 are the skeleton, the candidate and the receipt. Every table
here keys on an enum this system minted. Nothing reads what the user wrote.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    Committed,
    FactKind,
    PlanningSessionSnapshot,
    SkeletonPayload,
    TurnOutcome,
)

from .timebox_candidate import PendingTimeboxCandidates, ValidatedTimeboxCandidate
from .timeboxing_host import planning_timezone


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageLine(_Frozen):
    index: int = Field(ge=1, le=5)
    name: str
    #: What Proceed means on this stage, for the button and the receipt.
    next_action_label: str


STAGES: tuple[StageLine, ...] = (
    StageLine(index=1, name="Constraints", next_action_label="Confirm"),
    StageLine(index=2, name="Priorities", next_action_label="Plan the day"),
    StageLine(index=3, name="Sketch", next_action_label="Proceed"),
    StageLine(index=4, name="Refine", next_action_label="Commit"),
    StageLine(index=5, name="Commit", next_action_label="Done"),
)


def stage(index: int) -> StageLine:
    return STAGES[index - 1]


class ContextItem(_Frozen):
    text: str
    source: Literal["memory", "calendar", "user", "planner"]


class DecidedItem(_Frozen):
    text: str
    kind: Literal["assumption", "fact"]
    #: The fact or assumption id, so increment B can steer it by reference.
    ref: str


class Asking(_Frozen):
    requirement_id: str
    question: str
    why_needed: str
    options: list[BlockerOption] = Field(default_factory=list)


class ApproveControl(_Frozen):
    kind: Literal["approve"] = "approve"
    artifact_id: str
    artifact_revision: int
    artifact_digest: str


class DayTypeControl(_Frozen):
    """The date card's own controls: confirm, pick another day, override type."""

    kind: Literal["day_type"] = "day_type"
    user_id: str
    channel_id: str
    thread_ts: str
    planned_date: str
    tz_name: str


class CommitControl(_Frozen):
    kind: Literal["commit"] = "commit"
    candidate_id: str
    calendar_id: str | None
    day: str | None


class UndoControl(_Frozen):
    kind: Literal["undo"] = "undo"
    tx_id: str


class BackControl(_Frozen):
    kind: Literal["back"] = "back"


class CancelControl(_Frozen):
    kind: Literal["cancel"] = "cancel"


Control = Annotated[
    Union[
        ApproveControl,
        DayTypeControl,
        CommitControl,
        UndoControl,
        BackControl,
        CancelControl,
    ],
    Field(discriminator="kind"),
]


class StageCard(_Frozen):
    stage: StageLine
    session_key: str
    expected_revision: int
    context: list[ContextItem] = Field(default_factory=list)
    decided: list[DecidedItem] = Field(default_factory=list)
    asking: Asking | None = None
    #: The stage's own text: the skeleton markdown, the rendered candidate,
    #: the commit sentence. Empty on the date card, whose body is its controls.
    body: str = ""
    controls: list[Control] = Field(default_factory=list)
    #: Set only on a receipt: what happened to this card.
    done: str | None = None

    def as_receipt(self, done: str) -> StageCard:
        return self.model_copy(update={"controls": [], "asking": None, "done": done})


def date_stage_card(
    *,
    session_key: str,
    expected_revision: int,
    user_id: str,
    channel_id: str,
    thread_ts: str,
    planned_date: str,
    tz_name: str,
) -> StageCard:
    """Stage 1 as the date card. Shared with the day-reselect redraw so the
    redrawn card keeps the same header and the same controls."""

    return StageCard(
        stage=stage(1),
        session_key=session_key,
        expected_revision=expected_revision,
        # The receipt has no controls left to say which day was picked, so
        # the day is also the body. An ISO date, minted by the picker.
        body=f"Planning {planned_date}",
        controls=[
            DayTypeControl(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                planned_date=planned_date,
                tz_name=tz_name,
            ),
            CancelControl(),
        ],
    )


def empty_day_notice(snapshot: dict, patch: dict) -> str:
    """Say so when the candidate builds a whole day onto an empty read.

    On 2026-09-02 the read returned no blocks and the candidate added nineteen.
    The journal agrees the day was empty, so the plan was probably right -- but
    building an entire day from nothing is a decision, and the card presented
    it as a refinement. Decided over what tmbx minted: the snapshot's
    ``event_ids`` is every event the read saw, and each op's ``op`` is a
    schema value. Nothing here reads a title (#251).
    """

    event_ids = snapshot.get("event_ids")
    if not isinstance(event_ids, dict) or event_ids:
        return ""
    ops = patch.get("ops")
    if not isinstance(ops, list) or not ops:
        return ""
    added = sum(1 for op in ops if isinstance(op, dict) and op.get("op") == "add")
    return (
        ":information_source: The calendar for this day was *empty* when this "
        f"was drafted, so approving builds the whole day: {added} blocks added."
    )


#: Which stage a user-owned question belongs to, by the fact it asks for.
_QUESTION_STAGE: dict[FactKind, int] = {
    FactKind.DAY_FRAME: 1,
    FactKind.REQUESTED_ACTIVITY: 2,
    FactKind.ACTIVITY_READING: 2,
}

#: Which facts count as "decided" on a card, and how they are labelled.
#: DAY_FRAME is deliberately absent: it is already surfaced by the date card
#: at stage 1 (the planning day it locked), and repeating it here would show
#: the same fact twice under two different names.
_FACT_LABELS: dict[FactKind, str] = {
    FactKind.REQUESTED_ACTIVITY: "wanted",
    FactKind.ACTIVITY_READING: "read as",
}


def _decided(snapshot: PlanningSessionSnapshot) -> list[DecidedItem]:
    facts = [
        DecidedItem(
            text=f"{_FACT_LABELS[fact.kind]}: {_as_text(fact.value)}",
            kind="fact",
            ref=fact.fact_id,
        )
        for fact in snapshot.facts
        if fact.kind in _FACT_LABELS
    ]
    assumptions = [
        DecidedItem(
            text=f"{_as_text(assumption.value)} — {assumption.why_needed}",
            kind="assumption",
            ref=assumption.assumption_id,
        )
        for assumption in snapshot.assumptions
    ]
    return [*facts, *assumptions]


def _as_text(value: object) -> str:
    """One line for a JSON value. Presentation only: nothing reads it back."""

    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ", ".join(f"{key} {inner}" for key, inner in value.items())
    if isinstance(value, list):
        return ", ".join(_as_text(item) for item in value)
    return str(value)


def _nav(*, back: bool) -> list[Control]:
    controls: list[Control] = []
    if back:
        controls.append(BackControl())
    controls.append(CancelControl())
    return controls


def map_outcome(
    outcome: TurnOutcome,
    snapshot: PlanningSessionSnapshot,
    *,
    pending: PendingTimeboxCandidates,
    actor_user_id: str,
    session_key: str,
    channel_id: str,
    thread_ts: str,
) -> StageCard | None:
    """One outcome to one card, or None for the outcomes that are not stages.

    `TurnFailed`, `Cancelled` and `ArtifactReady` are left to `render_outcome`:
    a failure keeps the previous card live (its Retry is the way back), and a
    cancellation ends the session rather than advancing it.
    """

    if isinstance(outcome, AwaitingUser):
        blocker = snapshot.pending_blocker
        index = _QUESTION_STAGE.get(blocker.fact_kind, 2) if blocker else 2
        return StageCard(
            stage=stage(index),
            session_key=session_key,
            expected_revision=snapshot.revision,
            decided=_decided(snapshot),
            asking=Asking(
                requirement_id=outcome.requirement_id,
                question=outcome.question,
                why_needed=outcome.why_needed,
                options=list(outcome.options),
            ),
            controls=_nav(back=True),
        )

    if isinstance(outcome, AwaitingApproval):
        artifact = outcome.artifact
        if artifact.kind is ArtifactKind.PLANNING_DAY:
            payload = artifact.payload if isinstance(artifact.payload, dict) else {}
            return date_stage_card(
                session_key=session_key,
                expected_revision=snapshot.revision,
                user_id=actor_user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                planned_date=str(payload.get("date") or ""),
                tz_name=str(payload.get("timezone") or planning_timezone()),
            )
        if artifact.kind is ArtifactKind.SKELETON:
            # Loud on purpose: the submit gate refuses this shape, so a stored
            # skeleton that fails here predates the contract (#267) and must
            # not be drawn as an empty day.
            skeleton = SkeletonPayload.model_validate(artifact.payload)
            context = (
                [ContextItem(text=skeleton.reasoning, source="planner")]
                if skeleton.reasoning
                else []
            )
            return StageCard(
                stage=stage(3),
                session_key=session_key,
                expected_revision=snapshot.revision,
                context=context,
                decided=_decided(snapshot),
                body=skeleton.markdown,
                controls=[
                    ApproveControl(
                        artifact_id=artifact.artifact_id,
                        artifact_revision=artifact.revision,
                        artifact_digest=artifact.digest,
                    ),
                    *_nav(back=True),
                ],
            )
        if artifact.kind is ArtifactKind.VALIDATED_CANDIDATE:
            candidate = ValidatedTimeboxCandidate.from_artifact_payload(
                artifact.payload
            )
            owned = pending.replace(
                session_key, candidate, owner_user_id=actor_user_id
            )
            calendar_id = owned.snapshot.get("calendar_id")
            day = owned.snapshot.get("day")
            body = owned.rendered or "A validated plan is ready for your approval."
            notice = empty_day_notice(owned.snapshot, owned.patch)
            if notice:
                body = f"{notice}\n\n{body}"
            return StageCard(
                stage=stage(4),
                session_key=session_key,
                expected_revision=snapshot.revision,
                decided=_decided(snapshot),
                body=body,
                controls=[
                    CommitControl(
                        candidate_id=owned.candidate_id,
                        calendar_id=calendar_id if isinstance(calendar_id, str) else None,
                        day=day if isinstance(day, str) else None,
                    ),
                    *_nav(back=True),
                ],
            )
        return None

    if isinstance(outcome, Committed):
        payload = (
            outcome.receipt.payload if isinstance(outcome.receipt.payload, dict) else {}
        )
        tx_id = payload.get("tx_id")
        if payload.get("committed") is True and isinstance(tx_id, str) and tx_id:
            body = ":white_check_mark: Committed the plan you approved."
            if payload.get("durable") is not True:
                # A commit against the in-memory calendar is a true commit and
                # an empty day. Saying only "committed" here is what made an
                # unwired backend indistinguishable from a scheduled day.
                where = str(payload.get("calendar_backend") or "unknown")
                body = (
                    ":warning: Committed to the *"
                    f"{where}* calendar — nothing reached your real one."
                )
            controls: list[Control] = [UndoControl(tx_id=tx_id)]
        else:
            reason = str(payload.get("reason") or "commit_refused")
            body = f":warning: Nothing was committed — `{reason}`."
            controls = []
        return StageCard(
            stage=stage(5),
            session_key=session_key,
            expected_revision=snapshot.revision,
            body=body,
            controls=controls,
        )

    return None


__all__ = [
    "STAGES",
    "ApproveControl",
    "Asking",
    "BackControl",
    "CancelControl",
    "CommitControl",
    "ContextItem",
    "Control",
    "DayTypeControl",
    "DecidedItem",
    "StageCard",
    "StageLine",
    "UndoControl",
    "date_stage_card",
    "empty_day_notice",
    "map_outcome",
    "stage",
]
