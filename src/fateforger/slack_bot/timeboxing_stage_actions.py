"""Slack stage-control actions for deterministic timeboxing progression."""

from __future__ import annotations

from typing import Any, Literal

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId
from pydantic import BaseModel
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.agents.timeboxing.messages import TimeboxingStageAction
from fateforger.slack_bot.constraint_review import decode_metadata
from fateforger.slack_bot.timeboxing_submit import build_text_section_block

FF_TIMEBOX_STAGE_PROCEED_ACTION_ID = "ff_timebox_stage_proceed"
FF_TIMEBOX_STAGE_BACK_ACTION_ID = "ff_timebox_stage_back"
FF_TIMEBOX_STAGE_REDO_ACTION_ID = "ff_timebox_stage_redo"
FF_TIMEBOX_STAGE_CANCEL_ACTION_ID = "ff_timebox_stage_cancel"


class TimeboxingStageActionMeta(BaseModel):
    """Metadata encoded into stage-control button values."""

    channel_id: str
    thread_ts: str
    user_id: str

    @classmethod
    def from_value(cls, value: str) -> "TimeboxingStageActionMeta | None":
        """Parse stage-control metadata from encoded button value."""
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


class TimeboxingStageActionPayload(BaseModel):
    """Normalized Slack stage-action callback payload."""

    value: str
    prompt_channel_id: str
    prompt_ts: str
    actor_user_id: str | None = None

    @classmethod
    def from_action_body(
        cls, body: dict[str, Any]
    ) -> "TimeboxingStageActionPayload | None":
        """Extract typed payload fields from a Slack action body."""
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


def build_stage_actions_block(
    *,
    meta_value: str,
    can_proceed: bool,
    can_go_back: bool,
    include_cancel: bool = True,
) -> dict[str, Any]:
    """Build deterministic stage-control buttons for a timeboxing stage."""
    elements: list[dict[str, Any]] = []
    if can_proceed:
        elements.append(
            {
                "type": "button",
                "action_id": FF_TIMEBOX_STAGE_PROCEED_ACTION_ID,
                "text": {"type": "plain_text", "text": "Proceed"},
                "style": "primary",
                "value": meta_value,
            }
        )
    if can_go_back:
        elements.append(
            {
                "type": "button",
                "action_id": FF_TIMEBOX_STAGE_BACK_ACTION_ID,
                "text": {"type": "plain_text", "text": "Back"},
                "value": meta_value,
            }
        )
    elements.append(
        {
            "type": "button",
            "action_id": FF_TIMEBOX_STAGE_REDO_ACTION_ID,
            "text": {"type": "plain_text", "text": "Redo"},
            "value": meta_value,
        }
    )
    if include_cancel:
        elements.append(
            {
                "type": "button",
                "action_id": FF_TIMEBOX_STAGE_CANCEL_ACTION_ID,
                "text": {"type": "plain_text", "text": "Cancel"},
                "style": "danger",
                "value": meta_value,
            }
        )
    return {
        "type": "actions",
        "block_id": "ff_timebox_stage_actions",
        "elements": elements,
    }


class TimeboxingStageActionCoordinator:
    """Bridge stage-control Slack actions to typed runtime messages."""

    def __init__(self, *, runtime: Any, client: AsyncWebClient) -> None:
        """Initialize coordinator dependencies."""
        self._runtime = runtime
        self._client = client

    async def handle_action(
        self,
        *,
        payload: TimeboxingStageActionPayload,
        action: Literal["proceed", "back", "redo", "cancel"],
    ) -> None:
        """Handle a deterministic stage action and replace the prompt message."""
        meta = TimeboxingStageActionMeta.from_value(payload.value)
        if not meta:
            return
        in_progress_text = _stage_action_in_progress_text(action)
        await self._client.chat_update(
            channel=payload.prompt_channel_id,
            ts=payload.prompt_ts,
            text=in_progress_text,
            blocks=[build_text_section_block(text=in_progress_text)],
        )
        msg = TimeboxingStageAction(
            channel_id=meta.channel_id,
            thread_ts=meta.thread_ts,
            user_id=meta.user_id or (payload.actor_user_id or ""),
            action=action,
        )
        await self._dispatch_to_timeboxing(
            payload=payload,
            meta=meta,
            message=msg,
            failure_text="Stage action failed. Please try again.",
        )

    async def _dispatch_to_timeboxing(
        self,
        *,
        payload: TimeboxingStageActionPayload,
        meta: TimeboxingStageActionMeta,
        message: TimeboxingStageAction,
        failure_text: str,
    ) -> None:
        """Send stage-control message to runtime and update Slack in-place."""
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
    """Convert runtime results into Slack ``chat.update`` payload fields."""
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


def _stage_action_in_progress_text(
    action: Literal["proceed", "back", "redo", "cancel"],
) -> str:
    """Return short status text while a stage action is being processed."""
    labels = {
        "proceed": "Proceeding to the next stage...",
        "back": "Going back to the previous stage...",
        "redo": "Re-running this stage...",
        "cancel": "Stopping this timeboxing session...",
    }
    return labels.get(action, "Working on that...")


__all__ = [
    "FF_TIMEBOX_STAGE_PROCEED_ACTION_ID",
    "FF_TIMEBOX_STAGE_BACK_ACTION_ID",
    "FF_TIMEBOX_STAGE_REDO_ACTION_ID",
    "FF_TIMEBOX_STAGE_CANCEL_ACTION_ID",
    "TimeboxingStageActionCoordinator",
    "TimeboxingStageActionPayload",
    "TimeboxingStageActionMeta",
    "build_stage_actions_block",
]
