"""Slack UI constants + helpers for Timeboxing Stage 0 (date commit)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.slack_bot.constraint_review import decode_metadata, encode_metadata
from fateforger.slack_bot.ui import link_button
from fateforger.slack_bot.workspace import WorkspaceRegistry
from fateforger.agents.timeboxing.messages import TimeboxingCommitDate


FF_TIMEBOX_COMMIT_START_ACTION_ID = "ff_timebox_start"
FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID = "ff_timebox_pick_day"
FF_TIMEBOX_COMMIT_MODAL_CALLBACK_ID = "ff_timebox_commit_modal"


def _persona_payload(agent_type: str) -> dict[str, Any]:
    directory = WorkspaceRegistry.get_global()
    persona = directory.persona_for_agent(agent_type) if directory else None
    if not persona:
        return {}
    payload: dict[str, Any] = {}
    if persona.username:
        payload["username"] = persona.username
    if persona.icon_emoji:
        payload["icon_emoji"] = persona.icon_emoji
    if persona.icon_url:
        payload["icon_url"] = persona.icon_url
    return payload


def _day_options(*, tz: ZoneInfo, days: int = 14) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).astimezone(tz)
    base = now.date()
    options: list[dict[str, Any]] = []
    for offset in range(days):
        day = base + timedelta(days=offset)
        label = day.strftime("%a %d %b")
        if offset == 0:
            label = f"{label} (today)"
        elif offset == 1:
            label = f"{label} (tomorrow)"
        options.append({"text": {"type": "plain_text", "text": label}, "value": day.isoformat()})
    return options


def build_timebox_commit_modal(*, suggested_date: str, tz_name: str, private_metadata: str) -> dict[str, Any]:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    options = _day_options(tz=tz)
    initial = next((o for o in options if o.get("value") == suggested_date), options[0] if options else None)
    return {
        "type": "modal",
        "callback_id": FF_TIMEBOX_COMMIT_MODAL_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Timeboxing"},
        "submit": {"type": "plain_text", "text": "Start"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": private_metadata,
        "blocks": [
            {
                "type": "input",
                "block_id": "ff_timebox_day",
                "label": {"type": "plain_text", "text": "Which day?"},
                "element": {
                    "type": "static_select",
                    "action_id": "ff_timebox_day_select",
                    "options": options,
                    **({"initial_option": initial} if initial else {}),
                },
            }
        ],
    }


@dataclass(frozen=True)
class TimeboxCommitMeta:
    user_id: str
    channel_id: str
    thread_ts: str
    date: str
    tz: str

    @classmethod
    def from_value(cls, value: str) -> "TimeboxCommitMeta | None":
        meta = decode_metadata(value)
        channel_id = meta.get("channel_id") or ""
        thread_ts = meta.get("thread_ts") or ""
        user_id = meta.get("user_id") or ""
        date = meta.get("date") or ""
        tz = meta.get("tz") or "UTC"
        if not (channel_id and thread_ts and user_id and date):
            return None
        return cls(user_id=user_id, channel_id=channel_id, thread_ts=thread_ts, date=date, tz=tz)

    def to_private_metadata(self, *, prompt_channel_id: str, prompt_ts: str) -> str:
        return encode_metadata(
            {
                "user_id": self.user_id,
                "channel_id": self.channel_id,
                "thread_ts": self.thread_ts,
                "date": self.date,
                "tz": self.tz,
                "prompt_channel_id": prompt_channel_id,
                "prompt_ts": prompt_ts,
            }
        )


class TimeboxingCommitCoordinator:
    def __init__(self, *, runtime, client: AsyncWebClient) -> None:
        self._runtime = runtime
        self._client = client

    async def handle_start_action(
        self,
        *,
        value: str,
        prompt_channel_id: str,
        prompt_ts: str,
        actor_user_id: str | None,
    ) -> None:
        meta = TimeboxCommitMeta.from_value(value)
        if not meta:
            return

        planned_date = meta.date
        tz_name = meta.tz or "UTC"
        thread_key = f"{meta.channel_id}:{meta.thread_ts}"

        processing_payload: dict[str, Any] = {
            "channel": meta.channel_id,
            "thread_ts": meta.thread_ts,
            "text": ":hourglass_flowing_sand: *timeboxing_agent* is thinking...",
            **_persona_payload("timeboxing_agent"),
        }
        processing = await self._client.chat_postMessage(**processing_payload)

        try:
            result = await self._runtime.send_message(
                TimeboxingCommitDate(
                    channel_id=meta.channel_id,
                    thread_ts=meta.thread_ts,
                    user_id=meta.user_id or (actor_user_id or ""),
                    planned_date=planned_date,
                    timezone=tz_name,
                ),
                recipient=AgentId("timeboxing_agent", key=thread_key),
            )
        except Exception:
            await self._client.chat_update(
                channel=meta.channel_id,
                ts=processing["ts"],
                text=":warning: Something went wrong while starting timeboxing. Check bot logs.",
            )
            return

        payload = _slack_payload_from_result(result)
        update = {"channel": meta.channel_id, "ts": processing["ts"], "text": payload.get("text", "") or ""}
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await self._client.chat_update(**update)

        # Update the prompt message (DM/channel) with a "Go to Thread" button for convenience.
        try:
            perma = await self._client.chat_getPermalink(channel=meta.channel_id, message_ts=meta.thread_ts)
            link = perma.get("permalink") or ""
        except Exception:
            link = ""
        if link:
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "Timeboxing started. Continue in the thread:"}},
                {"type": "actions", "elements": [link_button(text="Go to Thread", url=link, action_id="ff_open_thread")]},
            ]
            try:
                await self._client.chat_update(channel=prompt_channel_id, ts=prompt_ts, text="Timeboxing started.", blocks=blocks)
            except Exception:
                pass

    async def handle_pick_day_action(
        self,
        *,
        trigger_id: str,
        value: str,
        prompt_channel_id: str,
        prompt_ts: str,
    ) -> None:
        meta = TimeboxCommitMeta.from_value(value)
        if not meta:
            return
        private_metadata = meta.to_private_metadata(prompt_channel_id=prompt_channel_id, prompt_ts=prompt_ts)
        view = build_timebox_commit_modal(
            suggested_date=meta.date,
            tz_name=meta.tz,
            private_metadata=private_metadata,
        )
        await self._client.views_open(trigger_id=trigger_id, view=view)

    async def handle_commit_modal_submission(
        self,
        *,
        private_metadata: str,
        state_values: dict,
        actor_user_id: str | None,
    ) -> None:
        meta = decode_metadata(private_metadata)
        channel_id = meta.get("channel_id") or ""
        thread_ts = meta.get("thread_ts") or ""
        user_id = meta.get("user_id") or (actor_user_id or "")
        tz_name = meta.get("tz") or "UTC"
        prompt_channel_id = meta.get("prompt_channel_id") or ""
        prompt_ts = meta.get("prompt_ts") or ""
        if not (channel_id and thread_ts and user_id):
            return

        picked = None
        try:
            picked = (
                state_values["ff_timebox_day"]["ff_timebox_day_select"]["selected_option"]["value"]
            )
        except Exception:
            picked = None
        if not picked:
            return

        value = encode_metadata({"channel_id": channel_id, "thread_ts": thread_ts, "user_id": user_id, "date": picked, "tz": tz_name})
        await self.handle_start_action(value=value, prompt_channel_id=prompt_channel_id, prompt_ts=prompt_ts, actor_user_id=actor_user_id)


def _slack_payload_from_result(result: Any) -> dict[str, Any]:
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


def _append_thread_button(blocks: list[dict[str, Any]], url: str) -> list[dict[str, Any]]:
    if not url:
        return blocks
    for block in blocks:
        if block.get("type") == "actions":
            elems = block.get("elements") or []
            if isinstance(elems, list) and len(elems) < 5:
                elems.append(link_button(text="Go to Thread", url=url, action_id="ff_open_thread"))
                block["elements"] = elems
                return blocks
    blocks.append({"type": "actions", "elements": [link_button(text="Go to Thread", url=url, action_id="ff_open_thread")]})
    return blocks

__all__ = [
    "FF_TIMEBOX_COMMIT_START_ACTION_ID",
    "FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID",
    "FF_TIMEBOX_COMMIT_MODAL_CALLBACK_ID",
    "TimeboxingCommitCoordinator",
    "build_timebox_commit_modal",
]
