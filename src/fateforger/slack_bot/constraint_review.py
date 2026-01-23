"""Slack modal helpers for timeboxing constraint review."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode

import ultimate_notion as uno
from pydantic import BaseModel, ConfigDict, ValidationError

from fateforger.agents.timeboxing.preferences import (
    ConstraintScope,
    ConstraintStatus,
)

CONSTRAINT_ROW_REVIEW_ACTION_ID = "timeboxing_constraint_review"
CONSTRAINT_REVIEW_VIEW_CALLBACK_ID = "timeboxing_constraint_review_modal"
CONSTRAINT_DECISION_ACTION_ID = "constraint_decision"
CONSTRAINT_DESCRIPTION_ACTION_ID = "constraint_description"


def _coerce_option_value(value: object | None) -> str:
    """Coerce UNO/Pydantic/Enum-ish values into a lowercase string label."""
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name.strip().lower()
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str) and enum_value:
        return enum_value.strip().lower()
    return str(value).strip().lower()


class ConstraintReviewItem(BaseModel):
    """Pydantic DTO for rendering Slack constraint review UI from UNO or session models."""

    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        frozen=True,
        arbitrary_types_allowed=True,
    )

    id: int | str | None = None
    uid: str | None = None
    name: str | None = None
    description: str | None = None
    necessity: object | None = None
    status: object | None = None
    scope: object | None = None

    @classmethod
    def coerce(cls, constraint: "ConstraintReviewItem | uno.Page | object") -> "ConstraintReviewItem":
        """Build a review DTO from a session constraint or a UNO page-like object."""
        if isinstance(constraint, cls):
            return constraint
        try:
            return cls.model_validate(constraint, from_attributes=True)
        except ValidationError:
            if isinstance(constraint, dict):
                return cls.model_validate(constraint)
            raise

    def constraint_id(self) -> str:
        """Return the best-effort identifier for Slack metadata."""
        raw = self.id if self.id is not None else (self.uid or "")
        return str(raw or "")

    def necessity_value(self) -> str:
        """Return the necessity label (e.g. 'must', 'should')."""
        return _coerce_option_value(self.necessity) or "must"

    def scope_enum(self) -> ConstraintScope:
        """Return a ConstraintScope derived from UNO/enum/string scope values."""
        scope_value = _coerce_option_value(self.scope)
        if scope_value == ConstraintScope.PROFILE.value:
            return ConstraintScope.PROFILE
        if scope_value == ConstraintScope.DATESPAN.value:
            return ConstraintScope.DATESPAN
        return ConstraintScope.SESSION

    def status_enum(self) -> ConstraintStatus | None:
        """Return a ConstraintStatus derived from UNO/enum/string status values."""
        if isinstance(self.status, ConstraintStatus):
            return self.status
        status_value = _coerce_option_value(self.status)
        if status_value == ConstraintStatus.LOCKED.value:
            return ConstraintStatus.LOCKED
        if status_value == ConstraintStatus.PROPOSED.value:
            return ConstraintStatus.PROPOSED
        if status_value == ConstraintStatus.DECLINED.value:
            return ConstraintStatus.DECLINED
        return None


def build_constraint_row_blocks(
    constraints: Iterable[object],
    *,
    thread_ts: str,
    user_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Build single-row constraint blocks with a review button."""
    items = [ConstraintReviewItem.coerce(constraint) for constraint in constraints]
    blocks: list[dict[str, Any]] = []
    for constraint in items[:limit]:
        if not constraint.constraint_id():
            continue
        value = encode_metadata(
            {
                "constraint_id": constraint.constraint_id(),
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
    constraint: object,
    *,
    channel_id: str,
    thread_ts: str,
    user_id: str,
) -> dict[str, Any]:
    """Build the Slack modal for reviewing a single constraint."""
    item = ConstraintReviewItem.coerce(constraint)
    metadata = encode_metadata(
        {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "user_id": user_id,
            "constraint_id": item.constraint_id(),
        }
    )
    description = item.description or ""
    name = _single_line(item.name or "Constraint")
    scope_label = _constraint_scope_label(item)
    status_option = "decline" if item.status_enum() == ConstraintStatus.DECLINED else "accept"
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
                        f"*{name}* ({item.necessity_value()})\n"
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
                            '"always" or "from now on" in chat.'
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
                    "initial_option": (
                        {
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
                        }
                    ),
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
    state_values: dict[str, Any],
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


def _single_line(text: str) -> str:
    """Collapse whitespace into a single-line string."""
    return " ".join((text or "").split())


def _constraint_row_text(constraint: ConstraintReviewItem) -> str:
    """Render a single-line constraint description for Slack row blocks."""
    name = _single_line(constraint.name or "Constraint")
    description = _single_line(constraint.description or "")
    scope_label = _constraint_scope_label(constraint)
    if description:
        return f"*{name}* - {description} _(scope: {scope_label})_"
    return f"*{name}* _(scope: {scope_label})_"


def _constraint_scope_label(constraint: ConstraintReviewItem) -> str:
    """Return a human-friendly scope label for the constraint."""
    scope = constraint.scope_enum()
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
