"""Every Slack control the timeboxing route draws, and what each press carries.

One module for the whole surface because the two halves of a control have to
agree: a button's payload is written here and checked by the kernel, and a
second encoder shaped like `artifact_action_value` would be a second place the
session key or the revision could go missing -- which is what makes a press
arriving late decidable at all.

Nothing here reaches a calendar, a store or a model. Given an outcome the kernel
produced, these functions say what the user sees; the router says where it goes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    ArtifactReady,
    AwaitingApproval,
    AwaitingUser,
    Cancelled,
    Committed,
    PlanningArtifact,
    PlanningSessionSnapshot,
    TurnFailed,
    TurnOutcome,
)

from .messages import (
    SLACK_MAX_BLOCK_TEXT_CHARS,
    SLACK_MAX_TEXT_CHARS,
    SlackBlockMessage,
)
from .timebox_candidate import PendingTimeboxCandidates, ValidatedTimeboxCandidate
from .timeboxing_commit import build_timebox_date_card
from .timeboxing_host import planning_timezone
from .timeboxing_intents import ArtifactActionMeta

#: The one control that opens the commit gate.
FF_HARNESS_APPROVE_ACTION_ID = "ff_harness_approve"

class HarnessApproveActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_key: str
    candidate_id: str
    calendar_id: str | None = None
    day: str | None = None
    proposal_message_ts: str | None = None
    #: Set only by the kernel route, and the discriminator between the two.
    #: A press carrying a revision belongs to a planning session that must be
    #: told about its own commit; a press without one is the legacy path, where
    #: there is no session to tell. Absence is not a default -- it is the fact
    #: that no session was ever created for that thread.
    expected_revision: int | None = None


def harness_approve_block(
    thread_key: str,
    candidate_id: str,
    *,
    calendar_id: str | None = None,
    day: str | None = None,
    proposal_message_ts: str | None = None,
    expected_revision: int | None = None,
) -> dict:
    """The Approve control, offered beside a plan rather than after it.

    The payload carries target and message identity so a later process can
    recover the displayed draft exactly. Only the opaque candidate id grants
    authority to commit; the descriptive continuity fields never do.
    """
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "action_id": FF_HARNESS_APPROVE_ACTION_ID,
                "style": "primary",
                "text": {"type": "plain_text", "text": "Approve this plan"},
                "value": HarnessApproveActionPayload(
                    thread_key=thread_key,
                    candidate_id=candidate_id,
                    calendar_id=calendar_id,
                    day=day,
                    proposal_message_ts=proposal_message_ts,
                    expected_revision=expected_revision,
                ).model_dump_json(),
            }
        ],
    }

FF_HARNESS_UNDO_ACTION_ID = "ff_harness_undo"


def harness_undo_block(tx_id: str) -> dict:
    """The Undo control, offered beside a commit that actually landed.

    Carries the transaction id in `value` rather than reversing "the last
    commit": a thread may commit more than once, and undoing whichever was most
    recent globally would reverse someone else's write from another thread.
    The id names exactly the write this message reported.
    """
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "action_id": FF_HARNESS_UNDO_ACTION_ID,
                "style": "danger",
                "text": {"type": "plain_text", "text": "Undo this change"},
                "value": tx_id,
            }
        ],
    }


def _undo_outcome_text(payload: dict) -> str:
    """What to say in Slack about tmbx's answer.

    Refusals are reported with their reason, never as a button that quietly did
    nothing. `plan_undo` declines when the day has drifted since the commit --
    its precondition is total, which is what makes the restore byte-exact -- and
    "someone changed the day, so this cannot be cleanly reversed" is a different
    problem from "that transaction does not exist", with a different remedy.
    """
    # tmbx answers an undo with `committed`, the same field a commit uses --
    # an undo is itself a write. Reading a field it never sets would report
    # every reversal, successful or refused, as a failure.
    if payload.get("committed"):
        return ":leftwards_arrow_with_hook: Reversed. The calendar is back to how it was before that commit."
    reason = str(payload.get("reason") or "").strip() or "unknown"
    detail = str(payload.get("message") or payload.get("raw") or "").strip()
    text = f":warning: Could not undo that change — `{reason}`."
    return f"{text}\n```{detail[:400]}```" if detail else text

#: The typed controls a planning artifact is reviewed with. The decision lives
#: in the encoded metadata, never in the action id -- two ids exist only
#: because Slack requires them to be unique within one message.
FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID = "ff_timebox_artifact_approve"
FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID = "ff_timebox_artifact_cancel"
FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID = "ff_timebox_artifact_retry"
#: One id for every offered option, the same way the day-type row uses one for
#: all five: which option was pressed belongs in the encoded metadata, and an
#: id that spelled out its own answer would be a second place the offer could
#: drift from the record the kernel checks it against.
FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID = "ff_timebox_blocker_option"

#: Slack refuses a button label longer than this, and the label is written by a
#: model rather than by this host, so it is capped where it is drawn.
SLACK_MAX_BUTTON_TEXT_CHARS = 75

TIMEBOX_TURN_FAILED_TEXT = (
    ":warning: I could not carry that planning step through, and nothing "
    "reached your calendar. Say it again and I will retry."
)

#: The same failure, on a day that has already been written. On 2026-09-02 the
#: sentence above went out ninety seconds after nineteen blocks had reached the
#: calendar, and the one reassurance it made was the one that was false. Which
#: of the two is shown is decided by the receipt in the session -- state this
#: system wrote -- never by the message that failed.
TIMEBOX_TURN_FAILED_AFTER_COMMIT_TEXT = (
    ":warning: I could not carry that change through. The day you approved is "
    "still on your calendar as it was committed; this change did not reach it. "
    "Tell me again what to change and I will retry."
)

#: Refusing to cancel a day that is already on the calendar. A `cancelled`
#: session over a written calendar would describe a day that does not exist.
TIMEBOX_SESSION_COMMITTED_TEXT = (
    "This day is already on your calendar, so I did not cancel anything. Tell "
    "me what to change and I will revise it."
)

#: A press the kernel would not honour. `stale_blocker_choice` covers three
#: causes on purpose -- no question is open, that is not the open question, that
#: was not one of its options -- so a crafted value learns nothing from which
#: one it hit. One sentence therefore has to be true of all three, and this is
#: it: whatever the press was aimed at, the session is no longer asking it.
TIMEBOX_STALE_CHOICE_TEXT = (
    "That question has already been answered, so I left everything as it is. "
    "Tell me what you want changed and I will ask again."
)

#: Failure codes worth their own sentence. The code is minted by this system,
#: so choosing between them reads nothing anybody wrote. Anything absent gets
#: the one stable sentence above, which is the right default: a message that
#: guessed at a code it did not recognise would be confidently wrong.
TIMEBOX_FAILURE_TEXTS = {
    "stale_blocker_choice": TIMEBOX_STALE_CHOICE_TEXT,
    "session_committed": TIMEBOX_SESSION_COMMITTED_TEXT,
    "nothing_to_go_back_to": (
        "This is the first step of the session, so there is nothing to go "
        "back to. Pick the day, or cancel."
    ),
}


def _has_committed(snapshot: PlanningSessionSnapshot | None) -> bool:
    """Whether anything this session did has reached the calendar.

    The receipt, not the status: a session reopened for a revision is `open`
    again and its first commit is no less on the calendar for it.
    """

    if snapshot is None:
        return False
    return any(
        artifact.kind is ArtifactKind.COMMIT_RECEIPT
        and isinstance(artifact.payload, dict)
        and artifact.payload.get("committed") is True
        for artifact in snapshot.artifacts
    )


def timebox_failure_message(
    code: str | None = None, *, snapshot: PlanningSessionSnapshot | None = None
) -> SlackBlockMessage:
    """One stable sentence, with the detail left where it belongs: the log.

    A provider payload pasted into a thread is unreadable and a leak at once,
    and the correlation fields are already on the log line beside this call.
    A refusal the user can act on gets its own sentence, chosen by the code the
    kernel minted rather than by anything they wrote. Which stable sentence is
    chosen by what the session's receipts say about the calendar.
    """
    default = (
        TIMEBOX_TURN_FAILED_AFTER_COMMIT_TEXT
        if _has_committed(snapshot)
        else TIMEBOX_TURN_FAILED_TEXT
    )
    text = TIMEBOX_FAILURE_TEXTS.get(code or "", default)
    return SlackBlockMessage(
        text=text,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    )


def artifact_action_value(
    *,
    session_key: str,
    expected_revision: int,
    decision: str,
    artifact: PlanningArtifact | None,
    requirement_id: str | None = None,
    option_id: str | None = None,
) -> str:
    """Encode one typed review decision bound to an exact artifact or question.

    One encoder for every control this route draws. A second one shaped like it
    would be a second place the session key and the revision could go missing,
    and those two are the whole reason a press arriving late is decidable.
    """

    fields: dict[str, object] = {
        "session_key": session_key,
        "expected_revision": expected_revision,
        "decision": decision,
    }
    if requirement_id is not None:
        fields["requirement_id"] = requirement_id
    if option_id is not None:
        fields["option_id"] = option_id
    if artifact is not None:
        fields.update(
            {
                "artifact_id": artifact.artifact_id,
                "artifact_revision": artifact.revision,
                "artifact_digest": artifact.digest,
            }
        )
    return ArtifactActionMeta.model_validate(fields).model_dump_json()


def render_date_card(
    artifact: PlanningArtifact,
    *,
    session_key: str,
    expected_revision: int,
    user_id: str,
    channel_id: str,
    thread_ts: str,
) -> SlackBlockMessage:
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    return build_timebox_date_card(
        session_key=session_key,
        expected_revision=expected_revision,
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        planned_date=str(payload.get("date") or ""),
        tz_name=str(payload.get("timezone") or planning_timezone()),
    )


def render_skeleton(
    artifact: PlanningArtifact,
    *,
    snapshot: PlanningSessionSnapshot,
    session_key: str,
) -> SlackBlockMessage:
    """Present the skeleton and what the planner decided on its own.

    Assumptions are listed apart from anything being asked, because a choice
    the planner already made and a question it needs answered read the same
    when they share a paragraph -- and that is how a delegated decision came
    back as a question.
    """
    payload = artifact.payload if isinstance(artifact.payload, dict) else {}
    markdown = str(payload.get("markdown") or "").strip()
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*The shape of the day*\n{markdown}"[
                    :SLACK_MAX_BLOCK_TEXT_CHARS
                ],
            },
        }
    ]
    if snapshot.assumptions:
        chosen = "\n".join(
            f"• {assumption.value} — {assumption.why_needed}"
            for assumption in snapshot.assumptions[:10]
        )
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*I chose these myself*\n{chosen}"[
                        :SLACK_MAX_BLOCK_TEXT_CHARS
                    ],
                },
            }
        )
    blocks.append(
        {
            "type": "actions",
            "block_id": "ff_timebox_artifact_controls",
            "elements": [
                {
                    "type": "button",
                    "action_id": FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Proceed"},
                    "value": artifact_action_value(
                        session_key=session_key,
                        expected_revision=snapshot.revision,
                        decision="approve",
                        artifact=artifact,
                    ),
                },
                {
                    "type": "button",
                    "action_id": FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "value": artifact_action_value(
                        session_key=session_key,
                        expected_revision=snapshot.revision,
                        decision="cancel",
                        artifact=None,
                    ),
                },
            ],
        }
    )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Reply in this thread with anything you want changed.",
                }
            ],
        }
    )
    return SlackBlockMessage(
        text=f"The shape of the day\n{markdown}"[:SLACK_MAX_TEXT_CHARS],
        blocks=blocks,
    )


def render_question(
    outcome: AwaitingUser,
    *,
    session_key: str,
    expected_revision: int,
) -> SlackBlockMessage:
    """Put one open question, with its answers as buttons where it has any.

    An empty option list is the ordinary case and renders as a text box, not as
    a row of nothing: the catalog's one user-owned ask is what somebody wants
    out of their day, and that has no closed answer set to draw.

    Where the answers are known, the label is the button and the effect is the
    line beside it. They are different lengths and they answer different
    questions -- one names the choice, the other says what it costs -- so
    truncating the second onto the first loses the half that decides it.
    """

    text = f"{outcome.question}\n_{outcome.why_needed}_"
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text[:SLACK_MAX_BLOCK_TEXT_CHARS],
            },
        }
    ]
    if not outcome.options:
        return SlackBlockMessage(text=text[:SLACK_MAX_TEXT_CHARS], blocks=blocks)
    effects = "\n".join(
        f"*{option.label}* — {option.effect}" for option in outcome.options
    )
    blocks.append(
        {
            "type": "context",
            "block_id": "ff_timebox_blocker_effects",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": effects[:SLACK_MAX_BLOCK_TEXT_CHARS],
                }
            ],
        }
    )
    blocks.append(
        {
            "type": "actions",
            "block_id": "ff_timebox_blocker_options",
            "elements": [
                {
                    "type": "button",
                    "action_id": FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
                    "text": {
                        "type": "plain_text",
                        "text": option.label[:SLACK_MAX_BUTTON_TEXT_CHARS],
                    },
                    # The pair the kernel checks against the question it is
                    # holding. Neither is trusted for having been in a button.
                    "value": artifact_action_value(
                        session_key=session_key,
                        expected_revision=expected_revision,
                        decision="choose_option",
                        artifact=None,
                        requirement_id=outcome.requirement_id,
                        option_id=option.option_id,
                    ),
                }
                for option in outcome.options
            ],
        }
    )
    return SlackBlockMessage(
        text=f"{text}\n{effects}"[:SLACK_MAX_TEXT_CHARS], blocks=blocks
    )


def _empty_day_notice(snapshot: dict, patch: dict) -> str:
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


def render_candidate(
    artifact: PlanningArtifact,
    *,
    pending: PendingTimeboxCandidates,
    session_key: str,
    actor_user_id: str,
    expected_revision: int,
) -> SlackBlockMessage:
    """Show the validated candidate behind the one control that can commit it.

    The approve payload is the existing opaque candidate identity, so the
    commit gate stays the one already proven rather than a second one shaped
    like it.
    """
    candidate = ValidatedTimeboxCandidate.from_artifact_payload(artifact.payload)
    owned = pending.replace(session_key, candidate, owner_user_id=actor_user_id)
    calendar_id = owned.snapshot.get("calendar_id")
    day = owned.snapshot.get("day")
    text = owned.rendered or "A validated plan is ready for your approval."
    notice = _empty_day_notice(owned.snapshot, owned.patch)
    if notice:
        text = f"{notice}\n\n{text}"
    return SlackBlockMessage(
        text=text[:SLACK_MAX_TEXT_CHARS],
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text[:SLACK_MAX_BLOCK_TEXT_CHARS],
                },
            },
            harness_approve_block(
                session_key,
                owned.candidate_id,
                calendar_id=calendar_id if isinstance(calendar_id, str) else None,
                day=day if isinstance(day, str) else None,
                expected_revision=expected_revision,
            ),
        ],
    )


def render_failure(
    *,
    snapshot: PlanningSessionSnapshot,
    session_key: str,
    actor_user_id: str,
    code: str | None = None,
) -> SlackBlockMessage:
    """Say the one stable sentence, and offer the two moves that still exist.

    A failure code is minted by this system, so deciding what to offer here
    reads nothing anybody wrote. Retry repeats the turn verbatim, which is the
    whole value: without it the user has to retype what they already said to
    get back to the same place.

    Controls are withheld when they could only fail again -- a session that is
    already cancelled or committed has nothing to advance, and a session owned
    by somebody else will refuse this actor for the same reason it just did.
    """
    message = timebox_failure_message(code, snapshot=snapshot)
    if snapshot.status != "open" or snapshot.owner_user_id != actor_user_id:
        return message
    return SlackBlockMessage(
        text=message.text,
        blocks=[
            *message.blocks,
            {
                "type": "actions",
                "block_id": "ff_timebox_failure_controls",
                "elements": [
                    {
                        "type": "button",
                        "action_id": FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID,
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Try that again"},
                        "value": artifact_action_value(
                            session_key=session_key,
                            expected_revision=snapshot.revision,
                            decision="advance",
                            artifact=None,
                        ),
                    },
                    {
                        "type": "button",
                        "action_id": FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
                        "text": {"type": "plain_text", "text": "Cancel"},
                        "value": artifact_action_value(
                            session_key=session_key,
                            expected_revision=snapshot.revision,
                            decision="cancel",
                            artifact=None,
                        ),
                    },
                ],
            },
        ],
    )


def render_outcome(
    outcome: TurnOutcome,
    *,
    pending: PendingTimeboxCandidates,
    snapshot: PlanningSessionSnapshot,
    session_key: str,
    actor_user_id: str,
    channel_id: str,
    thread_ts: str,
    logger,
) -> SlackBlockMessage:
    """Turn exactly one domain outcome into exactly one Slack message."""

    if isinstance(outcome, TurnFailed):
        logger.warning(
            "adaptive timeboxing turn refused code=%s session_key=%s revision=%s",
            outcome.code,
            session_key,
            snapshot.revision,
        )
        return render_failure(
            snapshot=snapshot,
            session_key=session_key,
            actor_user_id=actor_user_id,
            code=outcome.code,
        )

    if isinstance(outcome, Cancelled):
        text = "Planning session cancelled. Nothing was written anywhere."
        return SlackBlockMessage(
            text=text,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        )

    if isinstance(outcome, AwaitingUser):
        return render_question(
            outcome,
            session_key=session_key,
            expected_revision=snapshot.revision,
        )

    if isinstance(outcome, AwaitingApproval):
        artifact = outcome.artifact
        if artifact.kind is ArtifactKind.PLANNING_DAY:
            return render_date_card(
                artifact,
                session_key=session_key,
                expected_revision=snapshot.revision,
                user_id=actor_user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
            )
        if artifact.kind is ArtifactKind.SKELETON:
            return render_skeleton(
                artifact, snapshot=snapshot, session_key=session_key
            )
        if artifact.kind is ArtifactKind.VALIDATED_CANDIDATE:
            return render_candidate(
                artifact,
                pending=pending,
                session_key=session_key,
                actor_user_id=actor_user_id,
                expected_revision=snapshot.revision,
            )

    if isinstance(outcome, Committed):
        payload = (
            outcome.receipt.payload
            if isinstance(outcome.receipt.payload, dict)
            else {}
        )
        tx_id = payload.get("tx_id")
        if payload.get("committed") is True and isinstance(tx_id, str) and tx_id:
            text = ":white_check_mark: Committed the plan you approved."
            if payload.get("durable") is not True:
                # A commit against the in-memory calendar is a true commit and
                # an empty day. Saying only "committed" here is what made an
                # unwired backend indistinguishable from a scheduled day.
                where = str(payload.get("calendar_backend") or "unknown")
                text = (
                    ":warning: Committed to the *"
                    f"{where}* calendar — nothing reached your real one."
                )
            return SlackBlockMessage(
                text=text,
                blocks=[
                    {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                    harness_undo_block(tx_id),
                ],
            )
        reason = str(payload.get("reason") or "commit_refused")
        text = f":warning: Nothing was committed — `{reason}`."
        return SlackBlockMessage(
            text=text,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        )

    if isinstance(outcome, ArtifactReady):
        text = f"Prepared the {outcome.artifact.kind.value.replace('_', ' ')}."
        return SlackBlockMessage(
            text=text,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        )

    logger.error(
        "adaptive timeboxing produced an unrenderable outcome kind=%s session_key=%s",
        getattr(outcome, "kind", "unknown"),
        session_key,
    )
    return timebox_failure_message()
