"""Slack submit/undo controls for timeboxing Stage 5 review flow."""

from __future__ import annotations

from typing import Any

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId
from pydantic import BaseModel
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.agents.timeboxing.messages import (
    TimeboxingCancelSubmit,
    TimeboxingConfirmSubmit,
    TimeboxingUndoSubmit,
)
from fateforger.slack_bot.constraint_review import decode_metadata

FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID = "ff_timebox_confirm_submit"
FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID = "ff_timebox_cancel_submit"
FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID = "ff_timebox_undo_submit"


class TimeboxSubmitMeta(BaseModel):
    """Metadata encoded into submit/undo Slack action values."""

    channel_id: str
    thread_ts: str
    user_id: str

    @classmethod
    def from_value(cls, value: str) -> "TimeboxSubmitMeta | None":
        """Parse metadata from a button value payload."""
        raw = decode_metadata(value)
        try:
            return cls.model_validate(
                {
                    "channel_id": raw.get("channel_id") or "",
                    "thread_ts": raw.get("thread_ts") or "",
                    "user_id": raw.get("user_id") or "",
                }
            )
        except Exception:
            return None


class TimeboxSubmitActionPayload(BaseModel):
    """Normalized Slack button action payload for submit/undo handlers."""

    value: str
    prompt_channel_id: str
    prompt_ts: str
    actor_user_id: str | None = None

    @classmethod
    def from_action_body(cls, body: dict[str, Any]) -> "TimeboxSubmitActionPayload | None":
        """Extract a typed action payload from a Slack action callback body."""
        actions = body.get("actions") or []
        action = actions[0] if isinstance(actions, list) and actions else {}
        value = action.get("value") if isinstance(action, dict) else ""
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        if not (value and channel_id and message_ts):
            return None
        try:
            return cls.model_validate(
                {
                    "value": str(value),
                    "prompt_channel_id": str(channel_id),
                    "prompt_ts": str(message_ts),
                    "actor_user_id": str(actor_user_id) if actor_user_id else None,
                }
            )
        except Exception:
            return None


def build_review_submit_actions_block(*, meta_value: str) -> dict[str, Any]:
    """Return the Stage 5 review submit/cancel action block."""
    return {
        "type": "actions",
        "block_id": "ff_timebox_review_actions",
        "elements": [
            {
                "type": "button",
                "action_id": FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID,
                "text": {"type": "plain_text", "text": "Submit to Calendar"},
                "style": "primary",
                "value": meta_value,
            },
            {
                "type": "button",
                "action_id": FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID,
                "text": {"type": "plain_text", "text": "Keep Editing"},
                "value": meta_value,
            },
        ],
    }


def build_undo_submit_actions_block(*, meta_value: str) -> dict[str, Any]:
    """Return the post-submit undo action block."""
    return {
        "type": "actions",
        "block_id": "ff_timebox_post_submit_actions",
        "elements": [
            {
                "type": "button",
                "action_id": FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID,
                "text": {"type": "plain_text", "text": "Undo"},
                "style": "danger",
                "value": meta_value,
            }
        ],
    }


def build_text_section_block(*, text: str) -> dict[str, Any]:
    """Render markdown text content as a Slack section block."""
    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": text or "(no response)"},
    }


class TimeboxingSubmitCoordinator:
    """Bridge submit/undo Slack button actions to timeboxing agent messages."""

    def __init__(self, *, runtime: Any, client: AsyncWebClient) -> None:
        """Initialize coordinator dependencies."""
        self._runtime = runtime
        self._client = client

    async def handle_confirm_action(
        self, *, payload: TimeboxSubmitActionPayload
    ) -> None:
        """Handle confirm button action by dispatching ``TimeboxingConfirmSubmit``."""
        meta = TimeboxSubmitMeta.from_value(payload.value)
        if not meta:
            return
        await self._client.chat_update(
            channel=payload.prompt_channel_id,
            ts=payload.prompt_ts,
            text="Submitting to calendar...",
            blocks=[build_text_section_block(text="Submitting to calendar...")],
        )
        msg = TimeboxingConfirmSubmit(
            channel_id=meta.channel_id,
            thread_ts=meta.thread_ts,
            user_id=meta.user_id or (payload.actor_user_id or ""),
        )
        await self._dispatch_to_timeboxing(
            payload=payload,
            meta=meta,
            message=msg,
            failure_text="Submission failed. Please try again.",
        )

    async def handle_cancel_action(
        self, *, payload: TimeboxSubmitActionPayload
    ) -> None:
        """Handle cancel button action by dispatching ``TimeboxingCancelSubmit``."""
        meta = TimeboxSubmitMeta.from_value(payload.value)
        if not meta:
            return
        msg = TimeboxingCancelSubmit(
            channel_id=meta.channel_id,
            thread_ts=meta.thread_ts,
            user_id=meta.user_id or (payload.actor_user_id or ""),
        )
        await self._dispatch_to_timeboxing(
            payload=payload,
            meta=meta,
            message=msg,
            failure_text="Cancel action failed. Please try again.",
        )

    async def handle_undo_action(
        self, *, payload: TimeboxSubmitActionPayload
    ) -> None:
        """Handle undo button action by dispatching ``TimeboxingUndoSubmit``."""
        meta = TimeboxSubmitMeta.from_value(payload.value)
        if not meta:
            return
        await self._client.chat_update(
            channel=payload.prompt_channel_id,
            ts=payload.prompt_ts,
            text="Undoing last submission...",
            blocks=[build_text_section_block(text="Undoing last submission...")],
        )
        msg = TimeboxingUndoSubmit(
            channel_id=meta.channel_id,
            thread_ts=meta.thread_ts,
            user_id=meta.user_id or (payload.actor_user_id or ""),
        )
        await self._dispatch_to_timeboxing(
            payload=payload,
            meta=meta,
            message=msg,
            failure_text="Undo failed. Please try again.",
        )

    async def _dispatch_to_timeboxing(
        self,
        *,
        payload: TimeboxSubmitActionPayload,
        meta: TimeboxSubmitMeta,
        message: TimeboxingConfirmSubmit | TimeboxingCancelSubmit | TimeboxingUndoSubmit,
        failure_text: str,
    ) -> None:
        """Send a typed message to the timeboxing runtime and update Slack."""
        thread_key = f"{meta.channel_id}:{meta.thread_ts}"
        try:
            result = await self._runtime.send_message(
                message,
                recipient=AgentId("timeboxing_agent", key=thread_key),
            )
        except Exception:
            await self._client.chat_update(
                channel=payload.prompt_channel_id,
                ts=payload.prompt_ts,
                text=failure_text,
                blocks=[build_text_section_block(text=f":warning: {failure_text}")],
            )
            return
        response_payload = _slack_payload_from_result(result)
        update: dict[str, Any] = {
            "channel": payload.prompt_channel_id,
            "ts": payload.prompt_ts,
            "text": response_payload.get("text", "") or "",
        }
        if response_payload.get("blocks"):
            update["blocks"] = response_payload["blocks"]
        await self._client.chat_update(**update)


def _slack_payload_from_result(result: Any) -> dict[str, Any]:
    """Convert agent result objects into Slack API payload fields."""
    chat_message = getattr(result, "chat_message", None) or result
    if hasattr(chat_message, "blocks") and hasattr(chat_message, "text"):
        blocks = getattr(chat_message, "blocks", None)
        text = getattr(chat_message, "text", None)
        if blocks is not None:
            return {"text": text or "", "blocks": blocks}
        return {"text": text or ""}
    content = getattr(chat_message, "content", None)
    if content is None and isinstance(result, TextMessage):
        content = result.content
    return {"text": content or "(no response)"}


__all__ = [
    "FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID",
    "FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID",
    "FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID",
    "TimeboxSubmitActionPayload",
    "TimeboxSubmitMeta",
    "TimeboxingSubmitCoordinator",
    "build_review_submit_actions_block",
    "build_undo_submit_actions_block",
    "build_text_section_block",
]
