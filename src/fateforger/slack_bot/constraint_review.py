"""Slack modal helpers for timeboxing constraint review."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode

from fateforger.agents.timeboxing.preferences import Constraint, ConstraintStatus

CONSTRAINT_REVIEW_ACTION_ID = "timeboxing_constraints_review"
CONSTRAINT_REVIEW_VIEW_CALLBACK_ID = "timeboxing_constraints_modal"
CONSTRAINT_DECISION_ACTION_ID = "constraint_decision"


def build_review_prompt_blocks(
    *,
    count: int,
    thread_ts: str,
    user_id: str,
) -> list[dict[str, Any]]:
    value = encode_metadata({"thread_ts": thread_ts, "user_id": user_id})
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"I extracted *{count}* scheduling constraints. Review them?",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": CONSTRAINT_REVIEW_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Review constraints"},
                    "value": value,
                }
            ],
        },
    ]


def build_constraint_review_view(
    constraints: Iterable[Constraint],
    *,
    channel_id: str,
    thread_ts: str,
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for constraint in constraints:
        blocks.append(_constraint_summary_block(constraint))
        blocks.append(_constraint_decision_block(constraint))

    return {
        "type": "modal",
        "callback_id": CONSTRAINT_REVIEW_VIEW_CALLBACK_ID,
        "private_metadata": encode_metadata(
            {"channel_id": channel_id, "thread_ts": thread_ts}
        ),
        "title": {"type": "plain_text", "text": "Constraint review"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks[:90],
    }


def parse_constraint_decisions(state_values: dict[str, Any]) -> dict[int, ConstraintStatus]:
    decisions: dict[int, ConstraintStatus] = {}
    for block_id, action_map in state_values.items():
        decision = action_map.get(CONSTRAINT_DECISION_ACTION_ID, {})
        selected = decision.get("selected_option")
        if not selected:
            continue
        value = selected.get("value")
        constraint_id = _constraint_id_from_block(block_id)
        if constraint_id is None:
            continue
        status = _status_from_value(value)
        if status:
            decisions[constraint_id] = status
    return decisions


def _constraint_summary_block(constraint: Constraint) -> dict[str, Any]:
    name = constraint.name
    necessity = constraint.necessity.value
    description = constraint.description
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*{name}* ({necessity})\n{description}",
        },
    }


def _constraint_decision_block(constraint: Constraint) -> dict[str, Any]:
    return {
        "type": "input",
        "block_id": f"constraint_{constraint.id}",
        "label": {"type": "plain_text", "text": "Decision"},
        "element": {
            "type": "radio_buttons",
            "action_id": CONSTRAINT_DECISION_ACTION_ID,
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
    }


def _constraint_id_from_block(block_id: str) -> int | None:
    if not block_id.startswith("constraint_"):
        return None
    raw = block_id.replace("constraint_", "", 1)
    try:
        return int(raw)
    except ValueError:
        return None


def _status_from_value(value: str | None) -> ConstraintStatus | None:
    if value == "accept":
        return ConstraintStatus.LOCKED
    if value == "decline":
        return ConstraintStatus.DECLINED
    return None


def encode_metadata(values: dict[str, str]) -> str:
    return urlencode(values)


def decode_metadata(payload: str) -> dict[str, str]:
    if not payload:
        return {}
    parsed = parse_qs(payload, keep_blank_values=True)
    return {key: value[0] for key, value in parsed.items()}


__all__ = [
    "CONSTRAINT_REVIEW_ACTION_ID",
    "CONSTRAINT_REVIEW_VIEW_CALLBACK_ID",
    "CONSTRAINT_DECISION_ACTION_ID",
    "build_review_prompt_blocks",
    "build_constraint_review_view",
    "parse_constraint_decisions",
    "encode_metadata",
    "decode_metadata",
]
