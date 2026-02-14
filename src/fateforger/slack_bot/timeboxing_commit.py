"""Slack UI constants + helpers for Timeboxing Stage 0 (date commit)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.agents.timeboxing.messages import TimeboxingCommitDate
from fateforger.slack_bot.constraint_review import decode_metadata, encode_metadata
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.ui import link_button
from fateforger.slack_bot.workspace import WorkspaceRegistry

FF_TIMEBOX_COMMIT_START_ACTION_ID = "ff_timebox_start"
FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID = "ff_timebox_day_select"


def _persona_payload(agent_type: str) -> dict[str, Any]:
    """Return Slack message persona overrides for a given agent type."""
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


def _iter_days(start: date, *, count: int) -> list[date]:
    """Return a list of consecutive calendar days starting at `start`."""
    return [start + timedelta(days=offset) for offset in range(count)]


def _format_long_day(day: date) -> str:
    """Return a human-friendly full date label."""
    weekday = day.strftime("%A")
    month = day.strftime("%B")
    return f"{weekday} {day.day} {month}"


def _format_relative_long_day(*, day: date, today: date) -> str:
    """Return a human-friendly day label relative to `today`."""
    if day == today:
        return f"Today — {_format_long_day(day)}"
    if day == today + timedelta(days=1):
        return f"Tomorrow — {_format_long_day(day)}"
    return _format_long_day(day)


def format_relative_day_label(*, planned_date: str, tz_name: str) -> str:
    """Format `planned_date` as a relative label in the user's timezone."""
    # TODO(refactor): Use a Pydantic schema for planned date/timezone validation.
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    today = datetime.now(timezone.utc).astimezone(tz).date()
    try:
        day = date.fromisoformat(planned_date)
    except Exception:
        day = today
    return _format_relative_long_day(day=day, today=today)


def _day_options(*, tz: ZoneInfo, days: int = 14) -> list[dict[str, Any]]:
    """Build Slack dropdown options for the next `days` calendar days."""
    now = datetime.now(timezone.utc).astimezone(tz)
    today = now.date()
    options: list[dict[str, Any]] = []
    for day in _iter_days(today, count=days):
        label = _format_relative_long_day(day=day, today=today)
        options.append(
            {"text": {"type": "plain_text", "text": label}, "value": day.isoformat()}
        )
    return options


def build_timebox_commit_prompt_message(
    *,
    planned_date: str,
    tz_name: str,
    meta_value: str,
) -> SlackBlockMessage:
    """Build the Stage-0 'confirm planned day' Slack message for timeboxing."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    today = datetime.now(timezone.utc).astimezone(tz).date()
    options = _day_options(tz=tz)
    initial = next(
        (o for o in options if o.get("value") == planned_date),
        options[0] if options else None,
    )
    display_day = format_relative_day_label(planned_date=planned_date, tz_name=tz_name)
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "block_id": "ff_timebox_commit_intro",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Before we start:* confirm which day you want to timebox.\n"
                    f"Suggested: *{display_day}*"
                ),
            },
        },
        {
            "type": "actions",
            "block_id": "ff_timebox_commit_controls",
            "elements": [
                {
                    "type": "static_select",
                    "action_id": FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
                    "placeholder": {"type": "plain_text", "text": "Pick a day"},
                    "options": options,
                    **({"initial_option": initial} if initial else {}),
                },
                {
                    "type": "button",
                    "action_id": FF_TIMEBOX_COMMIT_START_ACTION_ID,
                    "text": {"type": "plain_text", "text": "Confirm"},
                    "style": "primary",
                    "value": meta_value,
                },
            ],
        },
    ]
    return SlackBlockMessage(
        text=f"Confirm timeboxing day: {display_day}",
        blocks=blocks,
    )


@dataclass(frozen=True)
class TimeboxCommitMeta:
    """Encoded metadata passed through Slack interactive payloads."""

    user_id: str
    channel_id: str
    thread_ts: str
    date: str
    tz: str

    @classmethod
    def from_value(cls, value: str) -> "TimeboxCommitMeta | None":
        """Parse metadata encoded into Slack action values."""
        meta = decode_metadata(value)
        channel_id = meta.get("channel_id") or ""
        thread_ts = meta.get("thread_ts") or ""
        user_id = meta.get("user_id") or ""
        date = meta.get("date") or ""
        tz = meta.get("tz") or "UTC"
        if not (channel_id and thread_ts and user_id and date):
            return None
        return cls(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            date=date,
            tz=tz,
        )

    def to_private_metadata(self, *, prompt_channel_id: str, prompt_ts: str) -> str:
        """Encode metadata for Slack modal `private_metadata` round-trips."""
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
        """Create the coordinator that bridges Slack actions to the timeboxing agent."""
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
        """Handle the 'Confirm' button and dispatch `TimeboxingCommitDate` to the agent."""
        meta = TimeboxCommitMeta.from_value(value)
        if not meta:
            return

        planned_date = meta.date
        tz_name = meta.tz or "UTC"
        thread_key = f"{meta.channel_id}:{meta.thread_ts}"

        # Immediately update the prompt message to show loading state
        display_day = format_relative_day_label(
            planned_date=planned_date, tz_name=tz_name
        )
        loading_blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⏳ Starting timeboxing for *{display_day}*...",
                },
            }
        ]
        try:
            await self._client.chat_update(
                channel=prompt_channel_id,
                ts=prompt_ts,
                text=f"Starting timeboxing for {display_day}...",
                blocks=loading_blocks,
            )
        except Exception:
            pass

        processing_payload: dict[str, Any] = {
            "channel": meta.channel_id,
            "text": ":hourglass_flowing_sand: *timeboxing_agent* is thinking...",
            **_persona_payload("timeboxing_agent"),
        }
        # Only include thread_ts if it's a real message timestamp (not "dm")
        if meta.thread_ts and meta.thread_ts != "dm":
            processing_payload["thread_ts"] = meta.thread_ts
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
        update = {
            "channel": meta.channel_id,
            "ts": processing["ts"],
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await self._client.chat_update(**update)

        # Mark the session thread root as "in progress" once the user confirms.
        # Skip if thread_ts is "dm" (not a real message)
        display_day = format_relative_day_label(
            planned_date=planned_date, tz_name=tz_name
        )
        if meta.thread_ts and meta.thread_ts != "dm":
            try:
                await self._client.chat_update(
                    channel=meta.channel_id,
                    ts=meta.thread_ts,
                    text=f":large_blue_circle: Timeboxing session for {display_day}",
                )
            except Exception:
                pass

        # Update the prompt message (DM/channel) with a "Go to session" link for convenience.
        # Only show the link if the session is in a different channel (redirect case).
        link = ""
        is_redirect = prompt_channel_id != meta.channel_id
        if is_redirect and meta.thread_ts and meta.thread_ts != "dm":
            try:
                perma = await self._client.chat_getPermalink(
                    channel=meta.channel_id, message_ts=meta.thread_ts
                )
                link = perma.get("permalink") or ""
            except Exception:
                pass
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Timeboxing for *{display_day}* started.",
                },
            }
        ]
        if link:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        link_button(
                            text="Go to session",
                            url=link,
                            action_id="ff_open_thread",
                        )
                    ],
                }
            )
        try:
            await self._client.chat_update(
                channel=prompt_channel_id,
                ts=prompt_ts,
                text=f"Timeboxing for {display_day} started.",
                blocks=blocks,
            )
        except Exception:
            pass

    async def handle_day_select_action(
        self,
        *,
        prompt_channel_id: str,
        prompt_ts: str,
        selected_date: str,
        existing_meta_value: str,
    ) -> None:
        meta = TimeboxCommitMeta.from_value(existing_meta_value)
        if not meta:
            return
        # TODO(refactor): Validate selected_date with a Pydantic schema.
        try:
            date.fromisoformat(selected_date)
        except Exception:
            return
        value = encode_metadata(
            {
                "channel_id": meta.channel_id,
                "thread_ts": meta.thread_ts,
                "user_id": meta.user_id,
                "date": selected_date,
                "tz": meta.tz,
            }
        )
        prompt = build_timebox_commit_prompt_message(
            planned_date=selected_date, tz_name=meta.tz, meta_value=value
        )
        # Keep the session thread title aligned with the currently selected day.
        try:
            label = format_relative_day_label(
                planned_date=selected_date, tz_name=meta.tz
            )
            await self._client.chat_update(
                channel=meta.channel_id,
                ts=meta.thread_ts,
                text=f":large_yellow_circle: Timeboxing session for {label}",
            )
        except Exception:
            pass
        await self._client.chat_update(
            channel=prompt_channel_id,
            ts=prompt_ts,
            text=prompt.text,
            blocks=prompt.blocks,
        )


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


def _append_thread_button(
    blocks: list[dict[str, Any]], url: str
) -> list[dict[str, Any]]:
    if not url:
        return blocks
    for block in blocks:
        if block.get("type") == "actions":
            elems = block.get("elements") or []
            if isinstance(elems, list) and len(elems) < 5:
                elems.append(
                    link_button(
                        text="Go to session", url=url, action_id="ff_open_thread"
                    )
                )
                block["elements"] = elems
                return blocks
    blocks.append(
        {
            "type": "actions",
            "elements": [
                link_button(text="Go to session", url=url, action_id="ff_open_thread")
            ],
        }
    )
    return blocks


__all__ = [
    "FF_TIMEBOX_COMMIT_START_ACTION_ID",
    "FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID",
    "TimeboxingCommitCoordinator",
    "build_timebox_commit_prompt_message",
    "format_relative_day_label",
]
