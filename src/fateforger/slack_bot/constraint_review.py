"""Slack modal helpers for timeboxing constraint review."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode

from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintScope,
    ConstraintStatus,
)

CONSTRAINT_ROW_REVIEW_ACTION_ID = "timeboxing_constraint_review"
CONSTRAINT_REVIEW_VIEW_CALLBACK_ID = "timeboxing_constraint_review_modal"
CONSTRAINT_DECISION_ACTION_ID = "constraint_decision"
CONSTRAINT_DESCRIPTION_ACTION_ID = "constraint_description"


def build_constraint_row_blocks(
    constraints: Iterable[Constraint],
    *,
    thread_ts: str,
    user_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Build single-row constraint blocks with a review button."""
    items = list(constraints)
    blocks: list[dict[str, Any]] = []
    for constraint in items[:limit]:
        if constraint.id is None:
            continue
        value = encode_metadata(
            {
                "constraint_id": str(constraint.id),
                "thread_ts": thread_ts,
                "user_id": user_id,
            }
        )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _constraint_row_text(constraint)},
                "accessory": {
                    "type": "button",
                    "action_id": CONSTRAINT_ROW_REVIEW_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Review"},
                    "value": value,
                },
            }
        )
    remaining = len(items) - len(blocks)
    if remaining > 0:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"...and {remaining} more constraints."}
                ],
            }
        )
    return blocks


def build_constraint_review_view(
    constraint: Constraint,
    *,
    channel_id: str,
    thread_ts: str,
    user_id: str,
) -> dict[str, Any]:
    """Build the Slack modal for reviewing a single constraint."""
    metadata = encode_metadata(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "user_id": user_id,
            "constraint_id": str(constraint.id or ""),
        }
    )
    description = constraint.description or ""
    name = _single_line(constraint.name or "Constraint")
    scope_label = _constraint_scope_label(constraint)
    status_option = (
        "decline" if constraint.status == ConstraintStatus.DECLINED else "accept"
    )
    return {
        "type": "modal",
        "callback_id": CONSTRAINT_REVIEW_VIEW_CALLBACK_ID,
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "Constraint review"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{name}* ({constraint.necessity.value})\n"
                        f"_Scope: {scope_label}_"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "Edits here apply to this session unless you say "
                            "\"always\" or \"from now on\" in chat."
                        ),
                    }
                ],
            },
            {
                "type": "input",
                "block_id": "constraint_description_block",
                "label": {"type": "plain_text", "text": "Description"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": CONSTRAINT_DESCRIPTION_ACTION_ID,
                    "multiline": True,
                    "initial_value": description,
                },
            },
            {
                "type": "input",
                "block_id": "constraint_decision_block",
                "label": {"type": "plain_text", "text": "Decision"},
                "element": {
                    "type": "radio_buttons",
                    "action_id": CONSTRAINT_DECISION_ACTION_ID,
                    "initial_option": {
                        "text": {
                            "type": "plain_text",
                            "text": "Accept",
                        },
                        "value": "accept",
                    }
                    if status_option == "accept"
                    else {
                        "text": {
                            "type": "plain_text",
                            "text": "Decline",
                        },
                        "value": "decline",
                    },
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Accept"},
                            "value": "accept",
                        },
                        {
                            "text": {"type": "plain_text", "text": "Decline"},
                            "value": "decline",
                        },
                    ],
                },
            },
        ],
    }


def parse_constraint_review_submission(
    state_values: dict[str, Any]
) -> tuple[ConstraintStatus | None, str | None]:
    """Parse decision + description from a constraint review modal submission."""
    decision = (
        state_values.get("constraint_decision_block", {})
        .get(CONSTRAINT_DECISION_ACTION_ID, {})
        .get("selected_option")
    )
    status = _status_from_value((decision or {}).get("value"))
    description = (
        state_values.get("constraint_description_block", {})
        .get(CONSTRAINT_DESCRIPTION_ACTION_ID, {})
        .get("value")
    )
    cleaned = (description or "").strip()
    return status, cleaned or None


def _status_from_value(value: str | None) -> ConstraintStatus | None:
    """Translate UI decision values into constraint statuses."""
    if value == "accept":
        return ConstraintStatus.LOCKED
    if value == "decline":
        return ConstraintStatus.DECLINED
    return None


def _constraint_row_text(constraint: Constraint) -> str:
    """Render a single-line constraint description for Slack row blocks."""
    name = _single_line(constraint.name or "Constraint")
    description = _single_line(constraint.description or "")
    scope_label = _constraint_scope_label(constraint)
    if description:
        return f"*{name}* - {description} _(scope: {scope_label})_"
    return f"*{name}* _(scope: {scope_label})_"


def _single_line(text: str) -> str:
    """Collapse whitespace into a single-line string."""
    return " ".join((text or "").split())


def _constraint_scope_label(constraint: Constraint) -> str:
    """Return a human-friendly scope label for the constraint."""
    scope = constraint.scope or ConstraintScope.SESSION
    if scope == ConstraintScope.PROFILE:
        return "profile"
    if scope == ConstraintScope.DATESPAN:
        return "datespan"
    return "session"


def encode_metadata(values: dict[str, str]) -> str:
    """Encode modal metadata into a querystring value."""
    return urlencode(values)


def decode_metadata(payload: str) -> dict[str, str]:
    """Decode modal metadata from a querystring payload."""
    if not payload:
        return {}
    parsed = parse_qs(payload, keep_blank_values=True)
    return {key: value[0] for key, value in parsed.items()}


__all__ = [
    "CONSTRAINT_ROW_REVIEW_ACTION_ID",
    "CONSTRAINT_REVIEW_VIEW_CALLBACK_ID",
    "CONSTRAINT_DECISION_ACTION_ID",
    "CONSTRAINT_DESCRIPTION_ACTION_ID",
    "build_constraint_row_blocks",
    "build_constraint_review_view",
    "parse_constraint_review_submission",
    "encode_metadata",
    "decode_metadata",
]
