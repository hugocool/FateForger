from __future__ import annotations

import logging
import re
import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from slack_sdk.web.async_client import AsyncWebClient

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId

from fateforger.agents.schedular.messages import UpsertCalendarEvent
from fateforger.core.config import settings
from fateforger.haunt.planning_guardian import PlanningGuardian
from fateforger.haunt.planning_store import PlanningAnchorPayload, SqlAlchemyPlanningAnchorStore
from fateforger.haunt.reconcile import PlanningReconciler, PlanningReminder
from fateforger.slack_bot.constraint_review import decode_metadata, encode_metadata
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.ui import link_button
from fateforger.slack_bot.workspace import DEFAULT_PERSONAS, WorkspaceRegistry
from fateforger.slack_bot.workspace import WorkspaceRegistry

logger = logging.getLogger(__name__)

FF_PLANNING_SCHEDULE_ACTION_ID = "ff_planning_schedule_suggested"
FF_PLANNING_PICK_TIME_ACTION_ID = "ff_planning_pick_time"
FF_PLANNING_TIME_MODAL_CALLBACK_ID = "ff_planning_time_modal"

_PLANNING_TIME_INPUT_BLOCK_ID = "planning_time_input"
_PLANNING_TIME_ACTION_ID = "planning_time_select"

DEFAULT_PLANNING_DURATION_MINUTES = 30
DEFAULT_PLANNING_TIMEZONE = "Europe/Amsterdam"
DEFAULT_WORK_START = time(9, 0)
DEFAULT_WORK_END = time(18, 0)


@dataclass(frozen=True)
class PlanningSuggestion:
    start: datetime
    end: datetime
    tz: str


def planning_event_id_for_user(user_id: str) -> str:
    # The Google Calendar MCP server validates custom event IDs as base32hex:
    # lowercase letters a-v and digits 0-9 only (no hyphens/underscores).
    cleaned = re.sub(r"\s+", "", (user_id or "").strip().lower())
    digest = hashlib.sha1(cleaned.encode("utf-8")).digest()
    token = base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")
    return ("ffplanning" + token)[:64]


class PlanningCoordinator:
    def __init__(self, *, runtime: Any, focus: Any, client: AsyncWebClient) -> None:
        self._runtime = runtime
        self._focus = focus
        self._client = client
        self._anchor_store: SqlAlchemyPlanningAnchorStore | None = getattr(
            runtime, "planning_anchor_store", None
        )
        self._guardian: PlanningGuardian | None = getattr(runtime, "planning_guardian", None)
        self._reconciler: PlanningReconciler | None = getattr(
            runtime, "planning_reconciler", None
        )
        self._pending: dict[tuple[str, str], dict[str, str]] = {}
        # DM-friendly fallback: allow "pick another time" replies without requiring thread replies.
        self._pending_by_user: dict[tuple[str, str], dict[str, str]] = {}

    def attach_reconciler_dispatch(self) -> None:
        if not self._reconciler:
            return
        self._reconciler.set_dispatcher(self.dispatch_planning_reminder)

    async def ensure_anchor(
        self,
        *,
        user_id: str,
        channel_id: str | None,
        calendar_id: str = "primary",
    ) -> PlanningAnchorPayload:
        event_id = planning_event_id_for_user(user_id)
        if not self._anchor_store:
            return PlanningAnchorPayload(
                user_id=user_id, channel_id=channel_id, calendar_id=calendar_id, event_id=event_id
            )

        existing = await self._anchor_store.get(user_id=user_id)
        resolved_channel = channel_id or (existing.channel_id if existing else None)
        return await self._anchor_store.upsert(
            user_id=user_id,
            channel_id=resolved_channel,
            calendar_id=calendar_id,
            event_id=event_id,
        )

    async def maybe_register_user(self, *, user_id: str, channel_id: str, channel_type: str) -> None:
        if not user_id:
            return
        preferred_channel = channel_id if channel_type == "im" else None
        await self.ensure_anchor(user_id=user_id, channel_id=preferred_channel)
        if self._guardian:
            try:
                await self._guardian.reconcile_user(user_id=user_id)
            except Exception:
                logger.exception("Planning guardian reconcile_user failed for %s", user_id)

    async def dispatch_planning_reminder(self, reminder: PlanningReminder) -> None:
        user_id = reminder.user_id
        if not user_id:
            return

        directory = WorkspaceRegistry.get_global()
        admonishments_channel_id = (
            (directory.channel_for_name("admonishments") if directory else "") or ""
        ).strip()

        dm_channel = await self._resolve_dm_channel(user_id=user_id)
        if not dm_channel:
            return

        anchor = await self.ensure_anchor(user_id=user_id, channel_id=dm_channel)
        suggestion = await self._suggest_slot(anchor.calendar_id)

        blocks = self._build_reminder_blocks(reminder, anchor, suggestion)

        directory = WorkspaceRegistry.get_global()
        persona = (
            directory.persona_for_agent("admonisher_agent")
            if directory
            else DEFAULT_PERSONAS.get("admonisher_agent")
        )

        if admonishments_channel_id:
            try:
                payload = {
                    "channel": admonishments_channel_id,
                    "text": f"<@{user_id}>: {reminder.message}",
                }
                if persona and persona.username:
                    payload["username"] = persona.username
                if persona and persona.icon_emoji:
                    payload["icon_emoji"] = persona.icon_emoji
                if persona and persona.icon_url:
                    payload["icon_url"] = persona.icon_url
                log = await self._client.chat_postMessage(**payload)
                log_ts = log.get("ts")
                if log_ts:
                    perma = await self._client.chat_getPermalink(
                        channel=admonishments_channel_id, message_ts=log_ts
                    )
                    log_link = perma.get("permalink")
                    if log_link:
                        # Add a deep-link button to the DM prompt so the user can jump to the log thread.
                        for block in blocks:
                            if block.get("type") == "actions":
                                elems = block.get("elements") or []
                                if isinstance(elems, list) and len(elems) < 5:
                                    elems.append(
                                        link_button(
                                            text="Open log",
                                            url=log_link,
                                            action_id="ff_open_admonishments_log",
                                        )
                                    )
                                    block["elements"] = elems
                                break
            except Exception:
                logger.debug("Failed to post planning reminder to #admonishments", exc_info=True)

        payload = {"channel": dm_channel, "text": reminder.message, "blocks": blocks}
        if persona and persona.username:
            payload["username"] = persona.username
        if persona and persona.icon_emoji:
            payload["icon_emoji"] = persona.icon_emoji
        if persona and persona.icon_url:
            payload["icon_url"] = persona.icon_url
        resp = await self._client.chat_postMessage(**payload)
        root_ts = resp.get("ts")
        if root_ts and suggestion:
            meta = {
                "user_id": user_id,
                "calendar_id": anchor.calendar_id,
                "event_id": anchor.event_id,
                "start": suggestion.start.astimezone(timezone.utc).isoformat(),
                "end": suggestion.end.astimezone(timezone.utc).isoformat(),
                "tz": suggestion.tz,
                "prompt_ts": root_ts,
            }
            self._pending[(dm_channel, root_ts)] = meta
            self._pending_by_user[(dm_channel, user_id)] = meta

    async def handle_schedule_action(
        self, *, value: str, channel_id: str, thread_ts: str, actor_user_id: str | None
    ) -> None:
        metadata = self.decode_action_value(value)
        user_id = metadata.get("user_id") or actor_user_id or ""
        if not user_id:
            return

        calendar_id = metadata.get("calendar_id") or "primary"
        event_id = metadata.get("event_id") or planning_event_id_for_user(user_id)
        tz_name = metadata.get("tz") or DEFAULT_PLANNING_TIMEZONE
        tz = ZoneInfo(tz_name)

        start_utc = metadata.get("start")
        end_utc = metadata.get("end")
        if start_utc and end_utc:
            start = date_parser.isoparse(start_utc).astimezone(tz)
            end = date_parser.isoparse(end_utc).astimezone(tz)
        else:
            suggestion = await self._suggest_slot(calendar_id)
            if not suggestion:
                await self._update_prompt_message(
                    channel_id=channel_id,
                    message_ts=thread_ts,
                    text="I couldn't find a free 30m slot. Reply with a specific time (e.g. `tomorrow 10:00`).",
                )
                return
            start, end = suggestion.start, suggestion.end

        await self._schedule_planning_event(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            calendar_id=calendar_id,
            start=start,
            end=end,
            tz=tz,
        )

    async def handle_pick_time_action(self, *, channel_id: str, thread_ts: str) -> None:
        await self._client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Reply with a time like `today 16:00` or `tomorrow 10:00`.",
        )

    async def handle_pick_time_modal(
        self,
        *,
        trigger_id: str,
        value: str,
        channel_id: str,
        thread_ts: str,
        actor_user_id: str | None,
    ) -> None:
        metadata = self.decode_action_value(value)
        user_id = metadata.get("user_id") or actor_user_id or ""
        if not user_id:
            return

        calendar_id = metadata.get("calendar_id") or "primary"
        event_id = metadata.get("event_id") or planning_event_id_for_user(user_id)
        tz_name = metadata.get("tz") or DEFAULT_PLANNING_TIMEZONE
        tz = ZoneInfo(tz_name)

        start_utc = metadata.get("start")
        if start_utc:
            start = date_parser.isoparse(start_utc).astimezone(tz)
        else:
            suggestion = await self._suggest_slot(calendar_id)
            start = suggestion.start if suggestion else datetime.now(timezone.utc).astimezone(tz)

        initial_ts = int(start.astimezone(timezone.utc).timestamp())
        private_metadata = encode_metadata(
            {
                "user_id": user_id,
                "calendar_id": calendar_id,
                "event_id": event_id,
                "tz": tz.key,
                # The message we're updating lives at this ts.
                "prompt_ts": thread_ts,
                "channel_id": channel_id,
            }
        )
        view = _build_planning_time_modal(
            private_metadata=private_metadata,
            initial_date_time=initial_ts,
            tz=tz.key,
            duration_minutes=DEFAULT_PLANNING_DURATION_MINUTES,
        )
        await self._client.views_open(trigger_id=trigger_id, view=view)

    async def handle_time_modal_submission(
        self,
        *,
        private_metadata: str,
        state_values: dict[str, Any],
        actor_user_id: str | None,
    ) -> None:
        metadata = decode_metadata(private_metadata)
        user_id = metadata.get("user_id") or actor_user_id or ""
        channel_id = metadata.get("channel_id") or ""
        prompt_ts = metadata.get("prompt_ts") or ""
        if not (user_id and channel_id and prompt_ts):
            return

        calendar_id = metadata.get("calendar_id") or "primary"
        tz_name = metadata.get("tz") or DEFAULT_PLANNING_TIMEZONE
        tz = ZoneInfo(tz_name)

        picked = (
            (state_values.get(_PLANNING_TIME_INPUT_BLOCK_ID) or {})
            .get(_PLANNING_TIME_ACTION_ID, {})
            .get("selected_date_time")
        )
        if not picked:
            await self._update_prompt_message(
                channel_id=channel_id,
                message_ts=prompt_ts,
                text="No time was selected. Please try again.",
            )
            return

        start = datetime.fromtimestamp(int(picked), tz=timezone.utc).astimezone(tz)
        end = start + timedelta(minutes=DEFAULT_PLANNING_DURATION_MINUTES)

        await self._schedule_planning_event(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=prompt_ts,
            calendar_id=calendar_id,
            start=start,
            end=end,
            tz=tz,
        )

    async def maybe_handle_time_reply(
        self,
        *,
        user_id: str,
        channel_id: str,
        thread_ts: str | None,
        text: str,
    ) -> bool:
        pending: dict[str, str] | None
        if thread_ts:
            pending = self.pending_metadata(channel_id=channel_id, thread_ts=thread_ts)
        else:
            # In DMs, users often reply inline instead of in a thread; accept that.
            pending = self._pending_by_user.get((channel_id, user_id))
        if not pending:
            return False
        prompt_ts = pending.get("prompt_ts") or thread_ts
        if not prompt_ts:
            return False

        tz_name = pending.get("tz") or DEFAULT_PLANNING_TIMEZONE
        tz = ZoneInfo(tz_name)
        now = datetime.now(timezone.utc)

        cleaned = (text or "").strip().lower()
        if cleaned in {"yes", "y", "yep", "ok", "okay", "do it", "schedule it"}:
            start = date_parser.isoparse(pending["start"]).astimezone(tz)
            end = date_parser.isoparse(pending["end"]).astimezone(tz)
        else:
            parsed = self.parse_time_reply(text, now=now, tz=tz)
            if not parsed:
                return False
            start, end = parsed

        await self._schedule_planning_event(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=prompt_ts,
            calendar_id=pending.get("calendar_id") or "primary",
            start=start,
            end=end,
            tz=tz,
        )
        return True

    async def _schedule_planning_event(
        self,
        *,
        user_id: str,
        channel_id: str,
        thread_ts: str,
        calendar_id: str,
        start: datetime,
        end: datetime,
        tz: ZoneInfo,
    ) -> None:
        anchor = await self.ensure_anchor(user_id=user_id, channel_id=channel_id, calendar_id=calendar_id)
        await self._update_prompt_message(
            channel_id=channel_id,
            message_ts=thread_ts,
            text=f"Scheduling *Planning session* at {start.strftime('%a %H:%M')}–{end.strftime('%H:%M')} ({tz.key})…",
        )

        runtime_key = FocusManager.thread_key(channel_id, thread_ts=thread_ts, ts=thread_ts)
        result = await self._runtime.send_message(
            UpsertCalendarEvent(
                user_id=user_id,
                calendar_id=calendar_id,
                event_id=anchor.event_id,
                summary="Planning session",
                start=start.replace(tzinfo=None).isoformat(timespec="seconds"),
                end=end.replace(tzinfo=None).isoformat(timespec="seconds"),
                time_zone=tz.key,
                color_id="10",
            ),
            recipient=AgentId("planner_agent", key=runtime_key),
        )
        await self._update_prompt_message(
            channel_id=channel_id,
            message_ts=thread_ts,
            text=(result.text if isinstance(result, SlackBlockMessage) else _extract_text(result))
            or "Done.",
            blocks=(result.blocks if isinstance(result, SlackBlockMessage) else None),
        )

        self._pending.pop((channel_id, thread_ts), None)
        self._pending_by_user.pop((channel_id, user_id), None)
        if self._guardian:
            try:
                await self._guardian.reconcile_user(user_id=anchor.user_id)
            except Exception:
                logger.exception("Planning guardian reconcile_user failed after scheduling")

    async def _update_prompt_message(
        self,
        *,
        channel_id: str,
        message_ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        try:
            payload: dict[str, Any] = {"channel": channel_id, "ts": message_ts, "text": text}
            if blocks:
                payload["blocks"] = blocks
            await self._client.chat_update(**payload)
        except Exception:
            # Fallback: if the original message can't be updated, at least post a visible reply.
            try:
                is_dm = str(channel_id).startswith("D")
                payload = {"channel": channel_id, "text": text}
                if blocks:
                    payload["blocks"] = blocks
                if not is_dm:
                    payload["thread_ts"] = message_ts
                await self._client.chat_postMessage(**payload)
            except Exception:
                logger.exception("Failed to update or post planning prompt message")

    async def _resolve_dm_channel(self, *, user_id: str) -> str | None:
        try:
            dm = await self._client.conversations_open(users=[user_id])
            return (dm.get("channel") or {}).get("id")
        except Exception:
            logger.exception("Failed to open DM for %s", user_id)
            return None

    def _build_reminder_blocks(
        self,
        reminder: PlanningReminder,
        anchor: PlanningAnchorPayload,
        suggestion: PlanningSuggestion | None,
    ) -> list[dict[str, Any]]:
        suggested_text = ""
        meta: dict[str, str] = {
            "user_id": anchor.user_id,
            "calendar_id": anchor.calendar_id,
            "event_id": anchor.event_id,
        }
        if suggestion:
            local_start = suggestion.start.strftime("%a %H:%M")
            local_end = suggestion.end.strftime("%H:%M")
            suggested_text = f"\nSuggested: *{local_start}–{local_end}* ({suggestion.tz})"
            meta.update(
                {
                    "start": suggestion.start.astimezone(timezone.utc).isoformat(),
                    "end": suggestion.end.astimezone(timezone.utc).isoformat(),
                    "tz": suggestion.tz,
                }
            )

        value = encode_metadata(meta)
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{reminder.message}{suggested_text}\nEvent id: `{anchor.event_id}`",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": FF_PLANNING_SCHEDULE_ACTION_ID,
                        "text": {"type": "plain_text", "text": "Schedule planning session"},
                        "value": value,
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "action_id": FF_PLANNING_PICK_TIME_ACTION_ID,
                        "text": {"type": "plain_text", "text": "Pick a time"},
                        "value": value,
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Reply with a time (e.g. `tomorrow 10:00`) or press Schedule.",
                    }
                ],
            },
        ]

    async def _suggest_slot(self, calendar_id: str) -> PlanningSuggestion | None:
        if not self._reconciler:
            return None

        tz_name = getattr(settings, "planning_timezone", "") or DEFAULT_PLANNING_TIMEZONE
        tz = ZoneInfo(tz_name)
        now = datetime.now(timezone.utc).astimezone(tz)

        duration = timedelta(minutes=DEFAULT_PLANNING_DURATION_MINUTES)
        for day_offset in range(0, 2):
            day = (now + timedelta(days=day_offset)).date()
            window_start = datetime.combine(day, DEFAULT_WORK_START, tz)
            window_end = datetime.combine(day, DEFAULT_WORK_END, tz)
            if day_offset == 0:
                window_start = max(window_start, now + timedelta(minutes=5))
            if window_end <= window_start:
                continue

            events = await self._reconciler.calendar_client.list_events(
                calendar_id=calendar_id,
                time_min=window_start.astimezone(timezone.utc).isoformat(),
                time_max=window_end.astimezone(timezone.utc).isoformat(),
            )
            busy = _busy_intervals(events, tz)
            start = _first_gap(window_start, window_end, busy, duration)
            if start:
                end = start + duration
                return PlanningSuggestion(start=start, end=end, tz=tz.key)
        return None

    def pending_metadata(self, *, channel_id: str, thread_ts: str) -> dict[str, str] | None:
        return self._pending.get((channel_id, thread_ts))

    @staticmethod
    def parse_time_reply(text: str, *, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        try:
            parsed = date_parser.parse(cleaned, default=now.astimezone(tz), fuzzy=True)
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        start = parsed.astimezone(tz)
        end = start + timedelta(minutes=DEFAULT_PLANNING_DURATION_MINUTES)
        return start, end

    @staticmethod
    def decode_action_value(value: str) -> dict[str, str]:
        return decode_metadata(value)


def _parse_event_dt(raw: dict[str, Any] | None, *, tz: ZoneInfo) -> datetime | None:
    if not raw:
        return None
    if "dateTime" in raw and raw["dateTime"]:
        dt = date_parser.isoparse(raw["dateTime"])
        return dt.astimezone(tz)
    if "date" in raw and raw["date"]:
        day = date_parser.isoparse(raw["date"]).date()
        return datetime.combine(day, time(0, 0), tz)
    return None


def _busy_intervals(events: list[dict[str, Any]], tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    intervals: list[tuple[datetime, datetime]] = []
    for event in events:
        if (event.get("status") or "").lower() == "cancelled":
            continue
        start = _parse_event_dt(event.get("start"), tz=tz)
        end = _parse_event_dt(event.get("end"), tz=tz)
        if start and end and end > start:
            intervals.append((start, end))
    intervals.sort(key=lambda pair: pair[0])

    merged: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _first_gap(
    window_start: datetime,
    window_end: datetime,
    busy: list[tuple[datetime, datetime]],
    duration: timedelta,
) -> datetime | None:
    cursor = window_start
    for start, end in busy:
        if start - cursor >= duration:
            return cursor
        cursor = max(cursor, end)
        if cursor >= window_end:
            return None
    if window_end - cursor >= duration:
        return cursor
    return None


def _extract_text(result: Any) -> str:
    chat_message = getattr(result, "chat_message", None) or result
    content = getattr(chat_message, "content", None)
    if content is None:
        content = getattr(result, "content", None)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content or "").strip()


__all__ = [
    "FF_PLANNING_PICK_TIME_ACTION_ID",
    "FF_PLANNING_SCHEDULE_ACTION_ID",
    "FF_PLANNING_TIME_MODAL_CALLBACK_ID",
    "PlanningCoordinator",
    "planning_event_id_for_user",
]


def _build_planning_time_modal(
    *,
    private_metadata: str,
    initial_date_time: int,
    tz: str,
    duration_minutes: int,
) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": FF_PLANNING_TIME_MODAL_CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Schedule planning"},
        "submit": {"type": "plain_text", "text": "OK"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Pick a start time. Duration is fixed at *{duration_minutes}m*.\nTime zone: `{tz}`",
                },
            },
            {
                "type": "input",
                "block_id": _PLANNING_TIME_INPUT_BLOCK_ID,
                "label": {"type": "plain_text", "text": "Start time"},
                "element": {
                    "type": "datetimepicker",
                    "action_id": _PLANNING_TIME_ACTION_ID,
                    "initial_date_time": int(initial_date_time),
                },
            },
        ],
    }
