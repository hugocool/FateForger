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

from collections import Counter

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from fateforger.agents.timeboxing.elicitation import criterion_label, row_label
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    Committed,
    FactKind,
    Gate,
    GateMet,
    PlanningDay,
    PlanningSessionSnapshot,
    SkeletonPayload,
    TurnOutcome,
)

from .schedule_render import candidate_display_text
from .timebox_candidate import PendingTimeboxCandidates, ValidatedTimeboxCandidate


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
    #: Present for an assumption: who supplied it. A renderer that needs this
    #: reads the field, never the text.
    filed_by: Literal["planner", "user"] | None = None


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


class NextControl(_Frozen):
    """Consent to close Stage 1. Drawn only from a `GateMet` outcome."""

    kind: Literal["next"] = "next"


Control = Annotated[
    Union[
        ApproveControl,
        DayTypeControl,
        CommitControl,
        UndoControl,
        BackControl,
        CancelControl,
        NextControl,
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
    #: The gate line, always present on a Stage 1 card: what is still needed,
    #: or the proposal to close. Rendered from typed fields; nothing reads it.
    gate: str | None = None
    #: The stage's own text: the skeleton markdown, the rendered candidate,
    #: the commit sentence. Empty on the date card, whose body is its controls.
    body: str = ""
    controls: list[Control] = Field(default_factory=list)
    #: Set only on a receipt: what happened to this card.
    done: str | None = None

    def as_receipt(self, done: str) -> StageCard:
        """The same card, closed: no way left to act on a stage already left.

        Undo is the exception, and only Undo. Every other control asks the
        kernel to advance a stage that has moved on, so a press on one is at
        best refused; an undo names a write that reached the calendar, and it
        outlives the card that announced it. Dropping it meant a reopen-to-
        revise eighty seconds after the commit rewrote the stage-5 card to
        `5/5 · Commit — ✅ confirmed` and took away the only affordance for
        reversing the write -- the undo action id is drawn nowhere else.
        """

        kept: list[Control] = [
            control for control in self.controls if isinstance(control, UndoControl)
        ]
        return self.model_copy(
            update={"controls": kept, "asking": None, "done": done}
        )


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


def commit_basis_notice(snapshot: dict, patch: dict) -> str:
    """Say what approving does to the day that is on the calendar.

    On 2026-09-02 the read returned no blocks and the candidate added nineteen.
    The journal agrees the day was empty, so the plan was probably right -- but
    building an entire day from nothing is a decision, and the card presented
    it as a refinement. On 2026-09-03 the opposite case said nothing at all: a
    patch onto a day that already held eleven blocks, three of them written by
    another session minutes earlier, renamed them on approval and the card
    gave no sign that anything was there to change. Both are decided over
    what tmbx minted: the snapshot's ``event_ids`` is every event the read
    saw, and each op's ``op`` is a schema value. Nothing here reads a title
    (#251).
    """

    ops = patch.get("ops")
    if not isinstance(ops, list) or not ops:
        return ""
    event_ids = snapshot.get("event_ids")
    if not isinstance(event_ids, dict):
        return ""
    counts = Counter(op.get("op") for op in ops if isinstance(op, dict))
    if not event_ids:
        return (
            ":information_source: The calendar for this day was *empty* when this "
            f"was drafted, so approving builds the whole day: {counts['add']} blocks added."
        )
    changes = [
        f"{counts[op]} {label}"
        for op, label in (
            ("add", "added"),
            ("update", "updated"),
            ("move", "moved"),
            ("remove", "removed"),
        )
        if counts[op]
    ]
    if not changes:
        return ""
    return (
        f":information_source: The calendar for this day already has {len(event_ids)} "
        f"blocks; approving changes it: {', '.join(changes)}."
    )


#: Which facts count as "decided" on a card, and how they are labelled.
#: DAY_FRAME is deliberately absent, and not because it is shown elsewhere --
#: nothing shows it: the date card shows the date it locked, never the wake
#: and sleep times. It is `context` by the spec's own division rather than a
#: decision the user made on this ladder, and context beyond the skeleton's
#: reasoning is increment B's work.
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
            filed_by=assumption.filed_by,
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


def _gate_line(gate: Gate) -> str:
    if not gate.open_cells:
        return (
            f"That's what I know to ask about a {gate.day_label}. "
            "Anything else, or shall I plan?"
        )
    needs = ", ".join(
        f"{row_label(cell.row)}, {criterion_label(cell.criterion)}" for cell in gate.open_cells
    )
    return f"Still need: {needs}."


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
        index = TimeboxRequirements.stage_of(outcome.requirement_id)
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
            gate=None if outcome.gate is None else _gate_line(outcome.gate),
            controls=_nav(back=True),
        )

    if isinstance(outcome, GateMet):
        return StageCard(
            stage=stage(1),
            session_key=session_key,
            expected_revision=snapshot.revision,
            decided=_decided(snapshot),
            gate=_gate_line(outcome.gate),
            controls=[NextControl(), *_nav(back=True)],
        )

    if isinstance(outcome, AwaitingApproval):
        artifact = outcome.artifact
        if artifact.kind is ArtifactKind.PLANNING_DAY:
            # Through the model, not through `.get`. The stored payload is a
            # `PlanningDay.model_dump(mode="json")`, so it is read back the way
            # it was written -- and a payload that is not one stops here rather
            # than drawing a date card with an empty date on it. JSON mode
            # because that is the shape on disk: the contracts are strict, and
            # strict Python-mode validation rejects the ISO date string a JSON
            # dump actually carries.
            day = PlanningDay.model_validate_json(json.dumps(artifact.payload))
            return date_stage_card(
                session_key=session_key,
                expected_revision=snapshot.revision,
                user_id=actor_user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                planned_date=day.date.isoformat(),
                tz_name=day.timezone,
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
            # A person reads a schedule, not a handle table (#272). An older
            # artifact without rows still shows the table; the model-facing
            # table is untouched, it is what the next turn patches against.
            body = candidate_display_text(owned) or "A validated plan is ready for your approval."
            notice = commit_basis_notice(owned.snapshot, owned.patch)
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
        # Loud, not `None`. The kernel only ever awaits approval of a planning
        # day, a skeleton or a candidate, so another kind here is a kernel this
        # mapper has not been taught. Returning `None` sent it to
        # `present_outcome`'s catch-all, which answers with a failure message
        # and no card -- and the turn then receipts the card the user is still
        # standing on as "✅ confirmed", which is the one thing that did not
        # happen.
        raise ValueError(
            f"no stage card is defined for an approval of {artifact.kind.value}"
        )

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
    "NextControl",
    "StageCard",
    "StageLine",
    "UndoControl",
    "date_stage_card",
    "commit_basis_notice",
    "map_outcome",
    "stage",
]
