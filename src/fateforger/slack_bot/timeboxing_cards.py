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
    ArtifactReady,
    Cancelled,
    PlanningArtifact,
    PlanningSessionSnapshot,
    NeedsAnotherTurn,
    TurnFailed,
    TurnOutcome,
    has_commit_receipt,
)

from .messages import (
    SLACK_MAX_BLOCK_TEXT_CHARS,
    SLACK_MAX_MODAL_BLOCKS,
    SLACK_MAX_TEXT_CHARS,
    SlackBlockMessage,
)
from .stage_cards import (
    ApproveControl,
    BackControl,
    CancelControl,
    CommitControl,
    Control,
    DayTypeControl,
    DenyControl,
    NextControl,
    StageCard,
    UndoControl,
    map_outcome,
)
from .stage_context import ContextFold, ContextPanel, FoldRow
from .timebox_candidate import PendingTimeboxCandidates
from .timeboxing_commit import build_timebox_date_card
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
#: Back is the same envelope with `decision: "back"`; the kernel decides what
#: "back" means from the artifacts it holds (#264).
FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID = "ff_timebox_artifact_back"
#: One id for every offered option, the same way the day-type row uses one for
#: all five: which option was pressed belongs in the encoded metadata, and an
#: id that spelled out its own answer would be a second place the offer could
#: drift from the record the kernel checks it against.
FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID = "ff_timebox_blocker_option"

#: The context panel's one control: opens the fold modal. What it means is
#: never executed as an `ArtifactActionMeta` decision -- the host opens a
#: modal instead of calling `intent_from_artifact_action` -- the value only
#: binds the press to its session.
FF_TIMEBOX_SHOW_RULES_ACTION_ID = "ff_timebox_show_rules"
#: One id for every steer menu in the fold, one per row. Which rule and which
#: verb were picked live in the encoded option, the same reason the blocker
#: options and the decided overflow share one id each.
FF_TIMEBOX_STEER_ACTION_ID = "ff_timebox_steer"
#: One id for every decided item's overflow menu.
FF_TIMEBOX_DECIDED_ACTION_ID = "ff_timebox_decided"

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
        if has_commit_receipt(snapshot)
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


#: Longer lists are cut by count, not by characters: the last item that fits
#: is a whole line, and the tail is one number.
STAGE_LIST_CAP = 8

#: The controls that accept a typed reply as well as a press.
_TYPING_STAGES = frozenset({1, 2, 3, 4})


def _section(text: str) -> dict:
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text[:SLACK_MAX_BLOCK_TEXT_CHARS]},
    }


def _bullets(title: str, lines: list[str]) -> dict:
    shown = [f"• {line}" for line in lines[:STAGE_LIST_CAP]]
    rest = len(lines) - len(shown)
    if rest > 0:
        shown.append(f"_+{rest} more_")
    return _section(f"*{title}*\n" + "\n".join(shown))


def _nav_button(action_id: str, label: str, value: str, *, primary: bool = False) -> dict:
    button = {
        "type": "button",
        "action_id": action_id,
        "text": {"type": "plain_text", "text": label[:SLACK_MAX_BUTTON_TEXT_CHARS]},
        "value": value,
    }
    if primary:
        button["style"] = "primary"
    return button


_NECESSITY_LABEL = {"must": "must", "should": "should"}
_APPLIES_LABEL = {"every_day": "every day", "some_days": "some days", "dated": "dated"}


def _row_tags(row: FoldRow) -> str:
    tags = [_NECESSITY_LABEL[row.necessity]]
    if row.suspended_reason is not None:
        tags.append(f"you said: {row.suspended_reason}")
    elif row.applies is not None:
        tags.append(_APPLIES_LABEL[row.applies])
    if row.also:
        tags.append("also " + ", ".join(row.also))
    return " · ".join(tags)


def _off_today_line(count: int, reason: str) -> str:
    if count == 0:
        return ""
    noun = "rule" if count == 1 else "rules"
    return f" · {count} {noun} off today because it is a {reason} day"


def render_context_panel(panel: ContextPanel) -> SlackBlockMessage:
    """Two blocks. Counts and group names are the only variable text, so the
    panel never grows and is safe to edit in place for the whole session."""

    summary = " · ".join(
        f"*{g.name if g.name is not None else 'no anchor'}* {len(g.uids)}" for g in panel.groups
    )
    head = (
        f"*1/5 · Constraints — what I know about a {panel.day_label}*\n"
        f"{panel.rule_count} rules apply ({panel.must_count} must, "
        f"{panel.rule_count - panel.must_count} should)"
        f"{_off_today_line(panel.off_today_count, panel.off_today_reason)}\n{summary}"
    )
    blocks: list[dict] = [
        {
            "type": "section",
            "block_id": "ff_timebox_context_panel",
            "text": {"type": "mrkdwn", "text": head[:SLACK_MAX_BLOCK_TEXT_CHARS]},
            "accessory": _nav_button(
                FF_TIMEBOX_SHOW_RULES_ACTION_ID,
                "Show rules",
                artifact_action_value(
                    session_key=panel.session_key,
                    expected_revision=panel.expected_revision,
                    decision="advance",  # never sent: show_rules is a host action; the value binds the session
                    artifact=None,
                ),
            ),
        }
    ]
    if panel.suspended:
        names = ", ".join(f"{s.name} (you said: {s.reason})" for s in panel.suspended)
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_Off for this session: {names}._"[:SLACK_MAX_BLOCK_TEXT_CHARS]}],
            }
        )
    else:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "_Nothing set aside for this session._"}]}
        )
    return SlackBlockMessage(text=head.splitlines()[0][:SLACK_MAX_TEXT_CHARS], blocks=blocks)


def _steer_option(fold: ContextFold, row: FoldRow, verb: str) -> dict:
    label = {"steer_not_today": "Not today", "steer_wrong": "This is wrong", "restore": "Restore"}[verb]
    fields = {
        "session_key": fold.session_key,
        "expected_revision": fold.expected_revision,
        "constraint_uid": row.uid,
    }
    if verb == "steer_wrong":
        fields.update(decision="steer_not_today", note="this is wrong")
    elif verb == "steer_not_today":
        fields.update(decision="steer_not_today")
    else:
        fields.update(decision="restore")
    return {
        "text": {"type": "plain_text", "text": label},
        # `exclude_none`: an overflow option's `value` is capped by Slack at
        # 150 chars, and the null fields this schema carries for every other
        # decision (artifact_id, option_id, ...) blow well past that on their
        # own -- `blockkit` catches it here rather than as a 400 in the thread.
        "value": ArtifactActionMeta.model_validate(fields).model_dump_json(exclude_none=True),
    }


def render_context_fold(fold: ContextFold) -> dict:
    """A modal view: one heading per group, one row per rule with its menu."""

    blocks: list[dict] = []
    for group in fold.groups:
        blocks.append(_section(f"*{group.name if group.name is not None else 'no anchor'}*"))
        for row in group.rows:
            name = f"~{row.name}~" if row.suspended_reason is not None else row.name
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{name}  _{_row_tags(row)}_"[:SLACK_MAX_BLOCK_TEXT_CHARS]},
                    "accessory": {
                        "type": "overflow",
                        "action_id": FF_TIMEBOX_STEER_ACTION_ID,
                        "options": [_steer_option(fold, row, verb) for verb in row.verbs],
                    },
                }
            )
    blocks.append(_section(f"*what today is not*{_off_today_line(fold.off_today_count, fold.off_today_reason) or ' · nothing is off today'}"))
    if fold.truncated:
        # Last, on purpose: the count of what got cut is the one thing a
        # truncated fold must not let scroll past unnoticed.
        rules, groups = fold.truncated
        blocks.append(_section(f"+{rules} rules in {groups} more groups — say the rule's name to steer it"))
    assert len(blocks) <= SLACK_MAX_MODAL_BLOCKS, len(blocks)
    return {
        "type": "modal",
        "callback_id": "ff_timebox_context_fold",
        "private_metadata": artifact_action_value(
            session_key=fold.session_key, expected_revision=fold.expected_revision, decision="advance", artifact=None
        ),
        "title": {"type": "plain_text", "text": f"Rules for {fold.day}"[:24]},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": blocks,
    }


def _decided_option(card: StageCard, control: Control) -> dict:
    if isinstance(control, DenyControl):
        return {
            "text": {"type": "plain_text", "text": "Deny"},
            "value": ArtifactActionMeta.model_validate(
                {
                    "session_key": card.session_key,
                    "expected_revision": card.expected_revision,
                    "decision": "deny_assumption",
                    "assumption_id": control.assumption_id,
                }
            ).model_dump_json(),
        }
    raise ValueError(f"no decided option for {control.kind}")


def render_stage_card(card: StageCard) -> SlackBlockMessage:
    """Draw one stage card, live or as a receipt, from its typed value.

    The header is the one thing every card and every receipt shares, so the
    thread reads as a ladder. A receipt is the same card with no controls and
    a `done` label -- what the user acted on, kept legible after the fact.

    The decided block count is now `1 + min(len, 8) + 1`, so the turn card's
    maximum is header 1 + decided 10 + divider 1 + asking 2 + hint 1 +
    options 1 + gate 1 + nav 1 + typing hint 1 = 19, under `SLACK_MAX_BLOCKS`
    (40).
    """

    header = f"*{card.stage.index}/5 · {card.stage.name}*"
    if card.done:
        header = f"{header}  —  {card.done}"
    blocks: list[dict] = [_section(header)]
    text_lines = [f"{card.stage.index}/5 · {card.stage.name}"]

    if card.context:
        blocks.append(_bullets("Context", [item.text for item in card.context]))
    if card.decided:
        blocks.append(_section("*Decided*"))
        shown = card.decided[:STAGE_LIST_CAP]
        for item in shown:
            block = _section(f"• {item.text}")
            if item.controls and card.done is None:
                block["accessory"] = {
                    "type": "overflow",
                    "action_id": FF_TIMEBOX_DECIDED_ACTION_ID,
                    "options": [_decided_option(card, control) for control in item.controls],
                }
            blocks.append(block)
        rest = len(card.decided) - len(shown)
        if rest > 0:
            blocks.append(_section(f"_+{rest} more_"))
    if card.body:
        blocks.append(_section(card.body))
        text_lines.append(card.body)

    if card.asking is not None:
        asking = card.asking
        blocks.append(_section(f"{asking.question}\n_{asking.why_needed}_"))
        text_lines.append(asking.question)
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Or tell me anything else about tomorrow; I will file it where it belongs._",
                    }
                ],
            }
        )
        if asking.options:
            effects = "\n".join(
                f"*{option.label}* — {option.effect}" for option in asking.options
            )
            blocks.append(
                {
                    "type": "context",
                    "block_id": "ff_timebox_blocker_effects",
                    "elements": [
                        {"type": "mrkdwn", "text": effects[:SLACK_MAX_BLOCK_TEXT_CHARS]}
                    ],
                }
            )
            blocks.append(
                {
                    "type": "actions",
                    "block_id": "ff_timebox_blocker_options",
                    "elements": [
                        _nav_button(
                            FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
                            option.label,
                            artifact_action_value(
                                session_key=card.session_key,
                                expected_revision=card.expected_revision,
                                decision="choose_option",
                                artifact=None,
                                requirement_id=asking.requirement_id,
                                option_id=option.option_id,
                            ),
                        )
                        for option in asking.options
                    ],
                }
            )

    if card.gate:
        blocks.append(_section(card.gate))
        text_lines.append(card.gate)

    nav: list[dict] = []
    for control in card.controls:
        if isinstance(control, DayTypeControl):
            date_card = build_timebox_date_card(
                session_key=card.session_key,
                expected_revision=card.expected_revision,
                user_id=control.user_id,
                channel_id=control.channel_id,
                thread_ts=control.thread_ts,
                planned_date=control.planned_date,
                tz_name=control.tz_name,
            )
            blocks.extend(date_card.blocks)
            text_lines.append(date_card.text)
        elif isinstance(control, CommitControl):
            blocks.append(
                harness_approve_block(
                    card.session_key,
                    control.candidate_id,
                    calendar_id=control.calendar_id,
                    day=control.day,
                    expected_revision=card.expected_revision,
                )
            )
        elif isinstance(control, UndoControl):
            blocks.append(harness_undo_block(control.tx_id))
        elif isinstance(control, ApproveControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
                    card.stage.next_action_label,
                    ArtifactActionMeta.model_validate(
                        {
                            "session_key": card.session_key,
                            "expected_revision": card.expected_revision,
                            "decision": "approve",
                            "artifact_id": control.artifact_id,
                            "artifact_revision": control.artifact_revision,
                            "artifact_digest": control.artifact_digest,
                        }
                    ).model_dump_json(),
                    primary=True,
                )
            )
        elif isinstance(control, NextControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID,
                    "Next",
                    artifact_action_value(
                        session_key=card.session_key,
                        expected_revision=card.expected_revision,
                        decision="advance",
                        artifact=None,
                    ),
                    primary=True,
                )
            )
        elif isinstance(control, BackControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
                    "Back",
                    artifact_action_value(
                        session_key=card.session_key,
                        expected_revision=card.expected_revision,
                        decision="back",
                        artifact=None,
                    ),
                )
            )
        elif isinstance(control, CancelControl):
            nav.append(
                _nav_button(
                    FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
                    "Cancel",
                    artifact_action_value(
                        session_key=card.session_key,
                        expected_revision=card.expected_revision,
                        decision="cancel",
                        artifact=None,
                    ),
                )
            )
    if nav:
        blocks.append(
            {
                "type": "actions",
                "block_id": "ff_timebox_artifact_controls",
                "elements": nav,
            }
        )
    if card.controls and card.done is None and card.stage.index in _TYPING_STAGES:
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
        text="\n".join(text_lines)[:SLACK_MAX_TEXT_CHARS], blocks=blocks
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


def present_outcome(
    outcome: TurnOutcome,
    *,
    pending: PendingTimeboxCandidates,
    snapshot: PlanningSessionSnapshot,
    session_key: str,
    actor_user_id: str,
    channel_id: str,
    thread_ts: str,
    logger,
) -> tuple[SlackBlockMessage, StageCard | None]:
    """One domain outcome to one Slack message, plus the card it was drawn from.

    The card is returned so the router can remember it and turn it into a
    receipt later; it is None for the outcomes that are not stages.
    """

    if isinstance(outcome, TurnFailed):
        logger.warning(
            "adaptive timeboxing turn refused code=%s session_key=%s revision=%s",
            outcome.code,
            session_key,
            snapshot.revision,
        )
        return (
            render_failure(
                snapshot=snapshot,
                session_key=session_key,
                actor_user_id=actor_user_id,
                code=outcome.code,
            ),
            None,
        )

    if isinstance(outcome, Cancelled):
        text = "Planning session cancelled. Nothing was written anywhere."
        return (
            SlackBlockMessage(
                text=text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            ),
            None,
        )

    card = map_outcome(
        outcome,
        snapshot,
        pending=pending,
        actor_user_id=actor_user_id,
        session_key=session_key,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    if card is not None:
        return render_stage_card(card), card

    if isinstance(outcome, ArtifactReady):
        text = f"Prepared the {outcome.artifact.kind.value.replace('_', ' ')}."
        return (
            SlackBlockMessage(
                text=text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            ),
            None,
        )

    if isinstance(outcome, NeedsAnotherTurn):
        # Not a failure. The work is kept and the planner said it is mid-fix,
        # so the fallback below -- "I could not carry that through, and
        # nothing reached your calendar" -- would be exactly wrong.
        text = (
            ":hourglass_flowing_sand: Still working on that one — "
            "say `go on` and I will pick up where I left off."
        )
        return (
            SlackBlockMessage(
                text=text,
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
            ),
            None,
        )

    logger.error(
        "adaptive timeboxing produced an unrenderable outcome kind=%s session_key=%s",
        getattr(outcome, "kind", "unknown"),
        session_key,
    )
    return timebox_failure_message(), None


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

    message, _card = present_outcome(
        outcome,
        pending=pending,
        snapshot=snapshot,
        session_key=session_key,
        actor_user_id=actor_user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        logger=logger,
    )
    return message
