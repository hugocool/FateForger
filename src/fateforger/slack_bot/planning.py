from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from autogen_core import AgentId
from dateutil import parser as date_parser
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.agents.schedular.messages import (
    SuggestedSlot,
    SuggestNextSlot,
    UpsertCalendarEvent,
    UpsertCalendarEventResult,
)
from fateforger.haunt.event_draft_store import (
    DraftStatus,
    EventDraftPayload,
    SqlAlchemyEventDraftStore,
)
from fateforger.haunt.planning_guardian import PlanningGuardian
from fateforger.haunt.planning_store import (
    PlanningAnchorPayload,
    SqlAlchemyPlanningAnchorStore,
)
from fateforger.haunt.reconcile import PlanningReconciler, PlanningReminder
from fateforger.haunt.timeboxing_activity import timeboxing_activity
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.workspace import DEFAULT_PERSONAS, WorkspaceRegistry

logger = logging.getLogger(__name__)


FF_EVENT_START_AT_ACTION_ID = "start_at"
FF_EVENT_START_DATE_ACTION_ID = "start_date"
FF_EVENT_START_TIME_ACTION_ID = "start_time"
FF_EVENT_DURATION_ACTION_ID = "duration_min"
FF_EVENT_ADD_ACTION_ID = "add_to_calendar"
FF_EVENT_ADD_DISABLED_ACTION_ID = "add_to_calendar_disabled"
FF_EVENT_RETRY_ACTION_ID = "retry_add_to_calendar"
FF_EVENT_OPEN_URL_ACTION_ID = "open_event_url"

FF_EVENT_BLOCK_DESC = "desc"
FF_EVENT_BLOCK_SUMMARY = "summary"
FF_EVENT_BLOCK_EDIT = "edit_controls"
FF_EVENT_BLOCK_STATUS = "status"

DEFAULT_PLANNING_TITLE = "Daily planning session"
DEFAULT_PLANNING_DESCRIPTION = "Plan tomorrow’s priorities and prep for shutdown."
DEFAULT_DURATION_OPTIONS = (15, 30, 45, 60, 90, 120)
DEFAULT_DURATION_MINUTES = 30
DEFAULT_TIMEZONE = "Europe/Amsterdam"


@dataclass(frozen=True)
class SlotSuggestion:
    start_utc: datetime
    end_utc: datetime
    tz: str


class PlanningCoordinator:
    def __init__(self, *, runtime: Any, focus: Any, client: AsyncWebClient) -> None:
        self._runtime = runtime
        self._focus = focus
        self._client = client
        self._anchor_store: SqlAlchemyPlanningAnchorStore | None = getattr(
            runtime, "planning_anchor_store", None
        )
        self._draft_store: SqlAlchemyEventDraftStore | None = getattr(
            runtime, "event_draft_store", None
        )
        self._guardian: PlanningGuardian | None = getattr(
            runtime, "planning_guardian", None
        )
        self._reconciler: PlanningReconciler | None = getattr(
            runtime, "planning_reconciler", None
        )
        if self._guardian:
            timeboxing_activity.set_on_idle(self._handle_timeboxing_idle)

    async def _handle_timeboxing_idle(self, user_id: str) -> None:
        if not self._guardian:
            return
        try:
            await self._guardian.reconcile_user(user_id=user_id)
        except Exception:
            logger.exception(
                "Planning guardian reconcile_user failed after idle for %s", user_id
            )

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
        from fateforger.slack_bot.planning_ids import planning_event_id_for_user

        event_id = planning_event_id_for_user(user_id)
        if not self._anchor_store:
            return PlanningAnchorPayload(
                user_id=user_id,
                channel_id=channel_id,
                calendar_id=calendar_id,
                event_id=event_id,
            )

        existing = await self._anchor_store.get(user_id=user_id)
        resolved_channel = channel_id or (existing.channel_id if existing else None)
        return await self._anchor_store.upsert(
            user_id=user_id,
            channel_id=resolved_channel,
            calendar_id=calendar_id,
            event_id=event_id,
        )

    async def maybe_register_user(
        self, *, user_id: str, channel_id: str, channel_type: str
    ) -> None:
        if not user_id:
            return
        preferred_channel = channel_id if channel_type == "im" else None
        await self.ensure_anchor(user_id=user_id, channel_id=preferred_channel)
        if self._guardian:
            try:
                await self._guardian.reconcile_user(user_id=user_id)
            except Exception:
                logger.exception(
                    "Planning guardian reconcile_user failed for %s", user_id
                )

    async def dispatch_planning_reminder(self, reminder: PlanningReminder) -> None:
        if not reminder.user_id:
            logger.debug("dispatch_planning_reminder: no user_id, skipping")
            return
        if timeboxing_activity.is_active(reminder.user_id):
            logger.info(
                "dispatch_planning_reminder: timeboxing active for %s; skipping",
                reminder.user_id,
            )
            return
        if not self._draft_store:
            logger.warning("event_draft_store not configured; skipping planning card")
            return

        logger.info(
            "dispatch_planning_reminder: starting for user %s", reminder.user_id
        )

        try:
            dm_channel = await self._resolve_dm_channel(user_id=reminder.user_id)
            if not dm_channel:
                logger.warning(
                    "dispatch_planning_reminder: could not resolve DM channel"
                )
                return

            anchor = await self.ensure_anchor(
                user_id=reminder.user_id, channel_id=dm_channel
            )
            logger.debug(
                "dispatch_planning_reminder: got anchor, suggesting next slot..."
            )

            # Use a timeout for slot suggestion to avoid blocking
            try:
                suggested = await asyncio.wait_for(
                    self._suggest_next_slot(calendar_id=anchor.calendar_id),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "dispatch_planning_reminder: slot suggestion timed out, using defaults"
                )
                suggested = None

            logger.debug(
                "dispatch_planning_reminder: got suggested slot: %s", suggested
            )
            start_utc = (
                suggested.start_utc
                if suggested
                else (datetime.now(timezone.utc) + timedelta(minutes=30))
            )
            end_utc = (
                suggested.end_utc
                if suggested
                else (start_utc + timedelta(minutes=DEFAULT_DURATION_MINUTES))
            )
            tz_name = (
                suggested.tz if suggested else DEFAULT_TIMEZONE
            ) or DEFAULT_TIMEZONE

            draft_id = f"draft_{uuid.uuid4().hex}"
            logger.debug("dispatch_planning_reminder: creating draft %s", draft_id)
            draft = await self._draft_store.create(
                draft_id=draft_id,
                user_id=reminder.user_id,
                channel_id=dm_channel,
                calendar_id=anchor.calendar_id,
                event_id=anchor.event_id,
                title=DEFAULT_PLANNING_TITLE,
                description=DEFAULT_PLANNING_DESCRIPTION,
                timezone=tz_name,
                start_at_utc=start_utc.isoformat(),
                duration_min=DEFAULT_DURATION_MINUTES,
            )
            logger.debug("dispatch_planning_reminder: draft created")

            status_lines = []
            if reminder.message:
                status_lines.append(reminder.message)
            status_lines.append("_Not added yet_")
            payload = _card_payload(draft, status_override="\n".join(status_lines))
            logger.debug(
                "dispatch_planning_reminder: card payload built with %d blocks",
                len(payload.get("blocks", [])),
            )

            # Post admonishment (with interactive card) to the admonishments channel
            await self._post_admonishment_log(reminder, card_payload=payload)
            logger.debug(
                "dispatch_planning_reminder: posted admonishment log with card"
            )

            # Get admonisher persona for the planning card
            directory = WorkspaceRegistry.get_global()
            persona = (
                directory.persona_for_agent("admonisher_agent")
                if directory
                else DEFAULT_PERSONAS.get("admonisher_agent")
            )
            post_kwargs: dict[str, Any] = {
                "channel": dm_channel,
                "text": payload["text"],
                "blocks": payload["blocks"],
            }
            if persona and persona.username:
                post_kwargs["username"] = persona.username
            if persona and persona.icon_emoji:
                post_kwargs["icon_emoji"] = persona.icon_emoji
            if persona and persona.icon_url:
                post_kwargs["icon_url"] = persona.icon_url

            logger.debug("dispatch_planning_reminder: posting card to DM...")
            resp = await self._client.chat_postMessage(**post_kwargs)
            message_ts = resp.get("ts")
            logger.info(
                "Posted planning card to DM: channel=%s, ts=%s, draft_id=%s",
                dm_channel,
                message_ts,
                draft.draft_id,
            )
            if message_ts:
                await self._draft_store.attach_message(
                    draft_id=draft.draft_id,
                    channel_id=dm_channel,
                    message_ts=message_ts,
                )
        except Exception:
            logger.exception(
                "dispatch_planning_reminder failed for user %s", reminder.user_id
            )

    async def _post_admonishment_log(
        self, reminder: PlanningReminder, *, card_payload: dict[str, Any] | None = None
    ) -> str | None:
        """Post the admonishment (with interactive card) to the admonishments channel and return permalink."""
        directory = WorkspaceRegistry.get_global()
        if not directory:
            return None

        admonishments_channel_id = (
            directory.channel_for_name("admonishments") or ""
        ).strip()
        if not admonishments_channel_id:
            logger.debug("No admonishments channel configured; skipping log")
            return None

        persona = directory.persona_for_agent(
            "admonisher_agent"
        ) or DEFAULT_PERSONAS.get("admonisher_agent")

        try:
            user_mention = f"<@{reminder.user_id}>" if reminder.user_id else "User"
            log_message = f"{user_mention}: {reminder.message}"

            post_kwargs: dict[str, Any] = {
                "channel": admonishments_channel_id,
                "text": log_message,
            }
            # Include the interactive card blocks if provided
            if card_payload and card_payload.get("blocks"):
                post_kwargs["blocks"] = card_payload["blocks"]
            if persona and persona.username:
                post_kwargs["username"] = persona.username
            if persona and persona.icon_emoji:
                post_kwargs["icon_emoji"] = persona.icon_emoji
            if persona and persona.icon_url:
                post_kwargs["icon_url"] = persona.icon_url

            res = await self._client.chat_postMessage(**post_kwargs)
            ts = res.get("ts")
            if ts:
                perma = await self._client.chat_getPermalink(
                    channel=admonishments_channel_id, message_ts=ts
                )
                return perma.get("permalink")
        except Exception:
            logger.debug("Failed to post planning admonishment log", exc_info=True)

        return None

    async def handle_start_at_changed(
        self,
        *,
        channel_id: str,
        message_ts: str,
        selected_date_time: int,
    ) -> EventDraftPayload | None:
        if not self._draft_store:
            return None
        start = datetime.fromtimestamp(int(selected_date_time), tz=timezone.utc)
        return await self._draft_store.update_time(
            channel_id=channel_id,
            message_ts=message_ts,
            start_at_utc=start.isoformat(),
        )

    async def handle_start_date_changed(
        self,
        *,
        channel_id: str,
        message_ts: str,
        selected_date: str,
    ) -> EventDraftPayload | None:
        if not self._draft_store:
            return None
        draft = await self._draft_store.get_by_message(
            channel_id=channel_id, message_ts=message_ts
        )
        if not draft:
            return None
        # TODO(refactor): Validate timezone/date inputs with Pydantic models.
        try:
            tz = ZoneInfo(draft.timezone or DEFAULT_TIMEZONE)
        except Exception:
            tz = ZoneInfo(DEFAULT_TIMEZONE)
        try:
            new_date = date_parser.isoparse(selected_date).date()
        except Exception:
            return None
        start_local = date_parser.isoparse(draft.start_at_utc).astimezone(tz)
        new_local = datetime(
            new_date.year,
            new_date.month,
            new_date.day,
            start_local.hour,
            start_local.minute,
            tzinfo=tz,
        )
        return await self._draft_store.update_time(
            channel_id=channel_id,
            message_ts=message_ts,
            start_at_utc=new_local.astimezone(timezone.utc).isoformat(),
        )

    async def handle_start_time_changed(
        self,
        *,
        channel_id: str,
        message_ts: str,
        selected_time: str,
    ) -> EventDraftPayload | None:
        if not self._draft_store:
            return None
        draft = await self._draft_store.get_by_message(
            channel_id=channel_id, message_ts=message_ts
        )
        if not draft:
            return None
        try:
            tz = ZoneInfo(draft.timezone or DEFAULT_TIMEZONE)
        except Exception:
            tz = ZoneInfo(DEFAULT_TIMEZONE)
        try:
            hour_str, minute_str = selected_time.split(":", 1)
            hour = int(hour_str)
            minute = int(minute_str)
        except Exception:
            return None
        start_local = date_parser.isoparse(draft.start_at_utc).astimezone(tz)
        new_local = start_local.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return await self._draft_store.update_time(
            channel_id=channel_id,
            message_ts=message_ts,
            start_at_utc=new_local.astimezone(timezone.utc).isoformat(),
        )

    async def handle_duration_changed(
        self,
        *,
        channel_id: str,
        message_ts: str,
        duration_min: int,
    ) -> EventDraftPayload | None:
        if not self._draft_store:
            return None
        return await self._draft_store.update_time(
            channel_id=channel_id,
            message_ts=message_ts,
            duration_min=duration_min,
        )

    async def start_add_to_calendar(
        self,
        *,
        draft_id: str,
        respond,
    ) -> None:
        if not self._draft_store:
            logger.warning(
                "start_add_to_calendar skipped: event_draft_store missing (draft_id=%s)",
                draft_id,
            )
            return
        draft = await self._draft_store.get_by_draft_id(draft_id=draft_id)
        if not draft:
            logger.warning(
                "start_add_to_calendar skipped: draft not found (draft_id=%s)", draft_id
            )
            return
        if draft.status in {DraftStatus.PENDING, DraftStatus.SUCCESS}:
            logger.info(
                "start_add_to_calendar no-op: draft already %s (draft_id=%s user_id=%s)",
                draft.status.value,
                draft.draft_id,
                draft.user_id,
            )
            return

        logger.info(
            "start_add_to_calendar: queueing add request (draft_id=%s user_id=%s calendar_id=%s event_id=%s start=%s duration_min=%s)",
            draft.draft_id,
            draft.user_id,
            draft.calendar_id,
            draft.event_id,
            draft.start_at_utc,
            draft.duration_min,
        )
        await self._draft_store.update_status(
            draft_id=draft.draft_id, status=DraftStatus.PENDING, last_error=None
        )
        pending = await self._draft_store.get_by_draft_id(draft_id=draft.draft_id)
        if pending:
            payload = _card_payload(pending, status_override="⏳ Adding to calendar…")
            await respond(
                text=payload["text"], blocks=payload["blocks"], replace_original=True
            )

        asyncio.create_task(
            self._add_to_calendar_async(draft_id=draft.draft_id, respond=respond)
        )

    async def _add_to_calendar_async(self, *, draft_id: str, respond) -> None:
        if not self._draft_store:
            logger.warning(
                "_add_to_calendar_async skipped: event_draft_store missing (draft_id=%s)",
                draft_id,
            )
            return
        draft = await self._draft_store.get_by_draft_id(draft_id=draft_id)
        if not draft:
            logger.warning("_add_to_calendar_async skipped: draft not found (%s)", draft_id)
            return

        try:
            tz = ZoneInfo(draft.timezone or DEFAULT_TIMEZONE)
        except Exception:
            tz = ZoneInfo(DEFAULT_TIMEZONE)

        start = date_parser.isoparse(draft.start_at_utc).astimezone(timezone.utc)
        end = start + timedelta(minutes=int(draft.duration_min))
        logger.info(
            "_add_to_calendar_async: dispatching upsert (draft_id=%s user_id=%s calendar_id=%s event_id=%s start=%s end=%s tz=%s)",
            draft.draft_id,
            draft.user_id,
            draft.calendar_id,
            draft.event_id,
            start.isoformat(),
            end.isoformat(),
            tz.key,
        )

        runtime_key = FocusManager.thread_key(
            draft.channel_id, thread_ts=draft.message_ts, ts=draft.message_ts or "root"
        )
        result = await self._runtime.send_message(
            UpsertCalendarEvent(
                calendar_id=draft.calendar_id,
                event_id=draft.event_id,
                summary=draft.title,
                description=draft.description,
                start=start.isoformat(),
                end=end.isoformat(),
                time_zone=tz.key,
                color_id="10",
            ),
            recipient=AgentId("planner_agent", key=runtime_key),
        )

        result_ok = isinstance(result, UpsertCalendarEventResult) and bool(result.ok)
        result_event_id = (
            (result.event_id or "").strip()
            if isinstance(result, UpsertCalendarEventResult)
            else ""
        )
        result_event_url = (
            (result.event_url or "").strip()
            if isinstance(result, UpsertCalendarEventResult)
            else ""
        )
        result_error = (
            (result.error or "").strip()
            if isinstance(result, UpsertCalendarEventResult)
            else ""
        )
        logger.info(
            "_add_to_calendar_async result: draft_id=%s type=%s ok=%s event_id=%s event_url_present=%s error=%s",
            draft.draft_id,
            type(result).__name__,
            result_ok,
            result_event_id,
            bool(result_event_url),
            result_error or None,
        )

        if (
            isinstance(result, UpsertCalendarEventResult)
            and result_ok
            and bool(result_event_url)
        ):
            await self._draft_store.update_status(
                draft_id=draft.draft_id,
                status=DraftStatus.SUCCESS,
                event_url=result_event_url,
                last_error=None,
            )
            updated = await self._draft_store.get_by_draft_id(draft_id=draft.draft_id)
            if updated:
                payload = _card_payload(updated)
                await respond(
                    text=payload["text"],
                    blocks=payload["blocks"],
                    replace_original=True,
                )
            if self._guardian:
                try:
                    await self._guardian.reconcile_user(user_id=draft.user_id)
                except Exception:
                    logger.exception(
                        "Planning guardian reconcile_user failed after scheduling"
                    )
            return

        error = result_error if result_error else None
        if result_ok and not result_event_url:
            error = (
                "Calendar upsert returned no event URL; insertion not confirmed. Please retry."
            )
            logger.warning(
                "_add_to_calendar_async strict-success failure: missing event URL despite ok=true (draft_id=%s event_id=%s)",
                draft.draft_id,
                result_event_id or draft.event_id,
            )
        if not error:
            error = "Calendar operation failed"
        await self._draft_store.update_status(
            draft_id=draft.draft_id, status=DraftStatus.FAILURE, last_error=str(error)
        )
        updated = await self._draft_store.get_by_draft_id(draft_id=draft.draft_id)
        if updated:
            payload = _card_payload(
                updated,
                status_override=f"⚠️ Not added: {updated.last_error or 'unknown error'}",
            )
            await respond(
                text=payload["text"], blocks=payload["blocks"], replace_original=True
            )

    async def refresh_card_for_message(
        self, *, channel_id: str, message_ts: str, respond
    ) -> None:
        if not self._draft_store:
            return
        draft = await self._draft_store.get_by_message(
            channel_id=channel_id, message_ts=message_ts
        )
        if not draft:
            return
        payload = _card_payload(draft, status_override=_status_text(draft))
        await respond(
            text=payload["text"], blocks=payload["blocks"], replace_original=True
        )

    async def _resolve_dm_channel(self, *, user_id: str) -> str | None:
        try:
            dm = await self._client.conversations_open(users=[user_id])
            return (dm.get("channel") or {}).get("id")
        except Exception:
            logger.exception("Failed to open DM for %s", user_id)
            return None

    async def _suggest_next_slot(self, *, calendar_id: str) -> SlotSuggestion | None:
        tz_name = DEFAULT_TIMEZONE
        runtime_key = FocusManager.thread_key(
            "dm", thread_ts=None, ts="planning_suggest"
        )
        result = await self._runtime.send_message(
            SuggestNextSlot(
                calendar_id=calendar_id,
                duration_min=DEFAULT_DURATION_MINUTES,
                time_zone=tz_name,
                horizon_days=2,
                work_start_hour=9,
                work_end_hour=18,
            ),
            recipient=AgentId("planner_agent", key=runtime_key),
        )
        if (
            isinstance(result, SuggestedSlot)
            and result.ok
            and result.start_utc
            and result.end_utc
        ):
            return SlotSuggestion(
                start_utc=date_parser.isoparse(result.start_utc).astimezone(
                    timezone.utc
                ),
                end_utc=date_parser.isoparse(result.end_utc).astimezone(timezone.utc),
                tz=result.time_zone or tz_name,
            )
        return None


def _status_text(draft: EventDraftPayload) -> str:
    if draft.status is DraftStatus.SUCCESS:
        if draft.event_url:
            return (
                "✅ Added to calendar\n"
                f"<{draft.event_url}|Open in Google Calendar>"
            )
        return "✅ Added to calendar"
    if draft.status is DraftStatus.FAILURE:
        reason = (draft.last_error or "unknown error").strip()
        return f"⚠️ Not added: {reason}"
    if draft.status is DraftStatus.PENDING:
        return "⏳ Adding to calendar…"
    return "Not added yet"


def _card_payload(
    draft: EventDraftPayload, *, status_override: str | None = None
) -> dict[str, Any]:
    tz = ZoneInfo(draft.timezone or DEFAULT_TIMEZONE)
    start = date_parser.isoparse(draft.start_at_utc).astimezone(tz)
    end = start + timedelta(minutes=int(draft.duration_min))

    when = f"{start.strftime('%a %H:%M')}"
    end_str = f"{end.strftime('%H:%M')}"
    duration_label = f"{int(draft.duration_min)} min"

    text = f"{draft.title} • {when} ({duration_label})"
    status_text = status_override or _status_text(draft)

    summary_text = f"*When*\n{when}–{end_str}\n*Duration*\n{duration_label}"

    def duration_option(minutes: int) -> dict[str, Any]:
        return {
            "text": {"type": "plain_text", "text": f"{minutes} min"},
            "value": str(minutes),
        }

    duration_options = [duration_option(m) for m in DEFAULT_DURATION_OPTIONS]
    initial_duration = duration_option(int(draft.duration_min))

    start_epoch = int(date_parser.isoparse(draft.start_at_utc).timestamp())
    start_at_element = {
        "type": "datetimepicker",
        "action_id": FF_EVENT_START_AT_ACTION_ID,
        "initial_date_time": start_epoch,
    }
    duration_element = {
        "type": "static_select",
        "action_id": FF_EVENT_DURATION_ACTION_ID,
        "initial_option": initial_duration,
        "options": duration_options,
    }

    if draft.status is DraftStatus.SUCCESS and draft.event_url:
        actions_block = {
            "type": "actions",
            "block_id": "post_add_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open event"},
                    "url": draft.event_url,
                    "action_id": FF_EVENT_OPEN_URL_ACTION_ID,
                }
            ],
        }
    else:
        if draft.status is DraftStatus.PENDING:
            action_id = FF_EVENT_ADD_DISABLED_ACTION_ID
            label = "Adding…"
        elif draft.status is DraftStatus.FAILURE:
            action_id = FF_EVENT_RETRY_ACTION_ID
            label = "Try again"
        else:
            action_id = FF_EVENT_ADD_ACTION_ID
            label = "Add to calendar"

        actions_block = {
            "type": "actions",
            "block_id": FF_EVENT_BLOCK_EDIT,
            "elements": [
                start_at_element,
                duration_element,
                {
                    "type": "button",
                    "action_id": action_id,
                    "text": {"type": "plain_text", "text": label},
                    "style": "primary",
                    "value": json.dumps({"draft_id": draft.draft_id}),
                }
            ],
        }

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": draft.title}},
        {
            "type": "section",
            "block_id": FF_EVENT_BLOCK_DESC,
            "text": {"type": "mrkdwn", "text": draft.description or ""},
        },
        {
            "type": "section",
            "block_id": FF_EVENT_BLOCK_SUMMARY,
            "text": {"type": "mrkdwn", "text": summary_text},
        },
    ]
    if draft.status is DraftStatus.SUCCESS and draft.event_url:
        blocks.append(actions_block)
    else:
        blocks.append(actions_block)
    blocks.append(
        {
            "type": "context",
            "block_id": FF_EVENT_BLOCK_STATUS,
            "elements": [{"type": "mrkdwn", "text": status_text}],
        }
    )
    return {"text": text, "blocks": blocks}


def parse_draft_id_from_value(value: str) -> str | None:
    # TODO(refactor): Validate the metadata payload with a Pydantic schema.
    try:
        obj = json.loads(value or "{}")
    except Exception:
        return None
    if isinstance(obj, dict):
        draft_id = obj.get("draft_id")
        return str(draft_id) if draft_id else None
    return None


__all__ = [
    "FF_EVENT_ADD_ACTION_ID",
    "FF_EVENT_ADD_DISABLED_ACTION_ID",
    "FF_EVENT_DURATION_ACTION_ID",
    "FF_EVENT_RETRY_ACTION_ID",
    "FF_EVENT_START_AT_ACTION_ID",
    "FF_EVENT_START_DATE_ACTION_ID",
    "FF_EVENT_START_TIME_ACTION_ID",
    "PlanningCoordinator",
    "parse_draft_id_from_value",
]
