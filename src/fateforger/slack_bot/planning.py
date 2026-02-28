from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
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
from fateforger.haunt.planning_session_store import (
    PlanningSessionStatus,
    SqlAlchemyPlanningSessionStore,
)
from fateforger.haunt.planning_store import (
    PlanningAnchorPayload,
    SqlAlchemyPlanningAnchorStore,
)
from fateforger.haunt.reconcile import PlanningReconciler, PlanningReminder
from fateforger.haunt.timeboxing_activity import timeboxing_activity
from fateforger.core.logging_config import (
    observe_stage_duration,
    record_admonishment_event,
    record_error,
    record_tool_call,
)
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
FF_EVENT_EDIT_ACTION_ID = "edit_event_details"
FF_EVENT_EDIT_MODAL_CALLBACK_ID = "ff_event_edit_modal"

FF_EVENT_BLOCK_DESC = "desc"
FF_EVENT_BLOCK_SUMMARY = "summary"
FF_EVENT_BLOCK_EDIT = "edit_controls"
FF_EVENT_BLOCK_STATUS = "status"
FF_EVENT_BLOCK_PICK_DATE = "pick_date"
FF_EVENT_BLOCK_PICK_TIME = "pick_time"

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
        self._planning_session_store: SqlAlchemyPlanningSessionStore | None = getattr(
            runtime, "planning_session_store", None
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
        total_started = perf_counter()
        preferred_channel = channel_id if channel_type == "im" else None
        ensure_anchor_started = perf_counter()
        try:
            await self.ensure_anchor(user_id=user_id, channel_id=preferred_channel)
        except asyncio.CancelledError:
            observe_stage_duration(
                stage="planning_register_ensure_anchor_cancelled",
                duration_s=perf_counter() - ensure_anchor_started,
            )
            record_error(component="planning_register", error_type="ensure_anchor_cancelled")
            raise
        else:
            observe_stage_duration(
                stage="planning_register_ensure_anchor",
                duration_s=perf_counter() - ensure_anchor_started,
            )

        if self._guardian:
            reconcile_started = perf_counter()
            try:
                await self._guardian.reconcile_user(user_id=user_id)
            except asyncio.CancelledError:
                observe_stage_duration(
                    stage="planning_guardian_reconcile_cancelled",
                    duration_s=perf_counter() - reconcile_started,
                )
                record_error(
                    component="planning_guardian", error_type="reconcile_cancelled"
                )
                raise
            except Exception:
                observe_stage_duration(
                    stage="planning_guardian_reconcile_error",
                    duration_s=perf_counter() - reconcile_started,
                )
                record_error(component="planning_guardian", error_type="reconcile_error")
                logger.exception(
                    "Planning guardian reconcile_user failed for %s", user_id
                )
            else:
                observe_stage_duration(
                    stage="planning_guardian_reconcile",
                    duration_s=perf_counter() - reconcile_started,
                )

        observe_stage_duration(
            stage="planning_register_user_total",
            duration_s=perf_counter() - total_started,
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
        """Persist a duration change for the event draft identified by channel+ts."""
        if not self._draft_store:
            return None
        return await self._draft_store.update_time(
            channel_id=channel_id,
            message_ts=message_ts,
            duration_min=duration_min,
        )

    async def get_draft(
        self,
        *,
        draft_id: str,
    ) -> EventDraftPayload | None:
        """Retrieve a draft by its draft_id."""
        if not self._draft_store:
            return None
        return await self._draft_store.get_by_draft_id(draft_id=draft_id)

    async def open_edit_modal(
        self,
        *,
        draft_id: str,
        trigger_id: str,
        client: Any,
    ) -> None:
        """Open the Edit modal for duration (and future fields) for a planning card draft."""
        draft = await self.get_draft(draft_id=draft_id)
        if not draft:
            logger.warning("open_edit_modal: draft not found (%s)", draft_id)
            return
        modal = _edit_modal_payload(draft)
        await client.views_open(trigger_id=trigger_id, view=modal)

    async def handle_edit_modal_submit(
        self,
        *,
        draft_id: str,
        duration_min: int,
        date_str: str | None = None,
    ) -> None:
        """Persist modal edits (duration + optional date) and refresh the planning card in-place."""
        draft = await self.get_draft(draft_id=draft_id)
        if not draft or not draft.channel_id or not draft.message_ts:
            logger.warning(
                "handle_edit_modal_submit: draft missing or no message coords (%s)",
                draft_id,
            )
            return
        if date_str:
            await self.handle_start_date_changed(
                channel_id=draft.channel_id,
                message_ts=draft.message_ts,
                selected_date=date_str,
            )
        await self.handle_duration_changed(
            channel_id=draft.channel_id,
            message_ts=draft.message_ts,
            duration_min=duration_min,
        )
        updated = await self.get_draft(draft_id=draft_id)
        if updated and updated.channel_id and updated.message_ts:
            payload = _card_payload(updated)
            await self._client.chat_update(
                channel=updated.channel_id,
                ts=updated.message_ts,
                text=payload["text"],
                blocks=payload["blocks"],
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
        record_admonishment_event(
            component="planning_card", event="add_to_calendar", status="queued"
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
            logger.warning(
                "_add_to_calendar_async skipped: draft not found (%s)", draft_id
            )
            return

        try:
            tz = ZoneInfo(draft.timezone or DEFAULT_TIMEZONE)
        except Exception:
            tz = ZoneInfo(DEFAULT_TIMEZONE)

        start_utc = date_parser.isoparse(draft.start_at_utc).astimezone(timezone.utc)
        end_utc = start_utc + timedelta(minutes=int(draft.duration_min))
        start_local = start_utc.astimezone(tz).replace(tzinfo=None, microsecond=0)
        end_local = end_utc.astimezone(tz).replace(tzinfo=None, microsecond=0)
        start_arg = start_local.isoformat()
        end_arg = end_local.isoformat()
        logger.info(
            "_add_to_calendar_async: dispatching upsert (draft_id=%s user_id=%s calendar_id=%s event_id=%s start=%s end=%s tz=%s)",
            draft.draft_id,
            draft.user_id,
            draft.calendar_id,
            draft.event_id,
            start_arg,
            end_arg,
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
                start=start_arg,
                end=end_arg,
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
            record_tool_call(agent="planning_card", tool="upsert_calendar_event", status="ok")
            record_admonishment_event(
                component="planning_card", event="add_to_calendar", status="ok"
            )
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
            if self._planning_session_store:
                try:
                    planned_day = start_utc.astimezone(tz).date()
                    await self._planning_session_store.upsert(
                        user_id=draft.user_id,
                        planned_date=planned_day,
                        calendar_id=draft.calendar_id,
                        event_id=result_event_id or draft.event_id,
                        status=PlanningSessionStatus.PLANNED,
                        title=draft.title,
                        event_url=result_event_url,
                        source="admonisher_planning_card",
                        channel_id=draft.channel_id,
                        thread_ts=draft.message_ts,
                    )
                except Exception:
                    logger.exception(
                        "Failed to upsert planning_session_ref for draft=%s",
                        draft.draft_id,
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
            error = "Calendar upsert returned no event URL; insertion not confirmed. Please retry."
            logger.warning(
                "_add_to_calendar_async strict-success failure: missing event URL despite ok=true (draft_id=%s event_id=%s)",
                draft.draft_id,
                result_event_id or draft.event_id,
            )
        if not error:
            error = "Calendar operation failed"
        _cal_error_type = (
            "calendar_no_event_url" if (result_ok and not result_event_url) else "calendar_upsert_failed"
        )
        record_error(component="planning_card", error_type=_cal_error_type)
        record_tool_call(agent="planning_card", tool="upsert_calendar_event", status="error")
        record_admonishment_event(
            component="planning_card", event="add_to_calendar", status="error"
        )
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
                "✅ Added to calendar\n" f"<{draft.event_url}|Open in Google Calendar>"
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
    """Build the planning card Slack Block Kit payload for a given draft.

    Layout (mobile-first):
    - Header: event title
    - Description section
    - Date section with datepicker accessory  (primary interactive element)
    - Time section with timepicker accessory  (primary interactive element)
    - Actions row: primary button + Edit button (opens modal for duration etc.)
    - Context footer: status
    """
    tz = ZoneInfo(draft.timezone or DEFAULT_TIMEZONE)
    start = date_parser.isoparse(draft.start_at_utc).astimezone(tz)
    end = start + timedelta(minutes=int(draft.duration_min))

    when = f"{start.strftime('%a %H:%M')}"
    end_str = f"{end.strftime('%H:%M')}"
    duration_label = f"{int(draft.duration_min)} min"

    text = f"{draft.title} • {when} ({duration_label})"
    status_text = status_override or _status_text(draft)

    # --- Determine primary action button ---
    if draft.status is DraftStatus.SUCCESS and draft.event_url:
        primary_button: dict[str, Any] = {
            "type": "button",
            "action_id": FF_EVENT_OPEN_URL_ACTION_ID,
            "text": {"type": "plain_text", "text": "Open event"},
            "url": draft.event_url,
        }
    elif draft.status is DraftStatus.PENDING:
        primary_button = {
            "type": "button",
            "action_id": FF_EVENT_ADD_DISABLED_ACTION_ID,
            "text": {"type": "plain_text", "text": "Adding…"},
            "style": "primary",
            "value": json.dumps({"draft_id": draft.draft_id}),
        }
    elif draft.status is DraftStatus.FAILURE:
        primary_button = {
            "type": "button",
            "action_id": FF_EVENT_RETRY_ACTION_ID,
            "text": {"type": "plain_text", "text": "Try again"},
            "style": "danger",
            "value": json.dumps({"draft_id": draft.draft_id}),
        }
    else:
        primary_button = {
            "type": "button",
            "action_id": FF_EVENT_ADD_ACTION_ID,
            "text": {"type": "plain_text", "text": "Add to calendar"},
            "style": "primary",
            "value": json.dumps({"draft_id": draft.draft_id}),
        }

    # --- Blocks ---
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": draft.title}},
        {
            "type": "section",
            "block_id": FF_EVENT_BLOCK_DESC,
            "text": {"type": "mrkdwn", "text": draft.description or ""},
        },
    ]

    # --- Time picker: first-class citizen on the card ---
    # Only shown when the event isn't already committed (SUCCESS).
    if draft.status is not DraftStatus.SUCCESS:
        blocks.append(
            {
                "type": "section",
                "block_id": FF_EVENT_BLOCK_PICK_TIME,
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Time* · {start.strftime('%a %-d %b')}",
                },
                "accessory": {
                    "type": "timepicker",
                    "action_id": FF_EVENT_START_TIME_ACTION_ID,
                    "initial_time": start.strftime("%H:%M"),
                    "placeholder": {"type": "plain_text", "text": "Pick a time"},
                },
            }
        )

    # --- Actions row: primary button + Edit (duration etc. are in the modal) ---
    action_elements: list[dict[str, Any]] = [primary_button]
    if draft.status is not DraftStatus.SUCCESS:
        action_elements.append(
            {
                "type": "button",
                "action_id": FF_EVENT_EDIT_ACTION_ID,
                "text": {"type": "plain_text", "text": "Edit"},
                "value": json.dumps({"draft_id": draft.draft_id}),
            }
        )
    blocks.append(
        {
            "type": "actions",
            "block_id": FF_EVENT_BLOCK_EDIT,
            "elements": action_elements,
        }
    )

    blocks.append(
        {
            "type": "context",
            "block_id": FF_EVENT_BLOCK_STATUS,
            "elements": [{"type": "mrkdwn", "text": status_text}],
        }
    )
    return {"text": text, "blocks": blocks}


def _edit_modal_payload(draft: EventDraftPayload) -> dict[str, Any]:
    """Build the Edit modal payload for adjusting duration and date.

    Time is the first-class element on the card itself; the Edit modal handles
    secondary controls: duration and date.
    """
    tz = ZoneInfo(draft.timezone or DEFAULT_TIMEZONE)
    start = date_parser.isoparse(draft.start_at_utc).astimezone(tz)

    def duration_option(minutes: int) -> dict[str, Any]:
        return {
            "text": {"type": "plain_text", "text": f"{minutes} min"},
            "value": str(minutes),
        }

    duration_options = [duration_option(m) for m in DEFAULT_DURATION_OPTIONS]
    cur_dur = int(draft.duration_min)
    if cur_dur not in DEFAULT_DURATION_OPTIONS:
        duration_options.insert(0, duration_option(cur_dur))
    initial_duration = duration_option(cur_dur)

    return {
        "type": "modal",
        "callback_id": FF_EVENT_EDIT_MODAL_CALLBACK_ID,
        "title": {"type": "plain_text", "text": "Edit session"},
        "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "private_metadata": json.dumps({"draft_id": draft.draft_id}),
        "blocks": [
            {
                "type": "input",
                "block_id": "date_input",
                "label": {"type": "plain_text", "text": "Date"},
                "element": {
                    "type": "datepicker",
                    "action_id": "date_select",
                    "initial_date": start.strftime("%Y-%m-%d"),
                    "placeholder": {"type": "plain_text", "text": "Select date"},
                },
            },
            {
                "type": "input",
                "block_id": "duration_input",
                "label": {"type": "plain_text", "text": "Duration"},
                "element": {
                    "type": "static_select",
                    "action_id": "duration_select",
                    "initial_option": initial_duration,
                    "options": duration_options,
                },
            },
        ],
    }


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
    "FF_EVENT_EDIT_ACTION_ID",
    "FF_EVENT_EDIT_MODAL_CALLBACK_ID",
    "FF_EVENT_RETRY_ACTION_ID",
    "FF_EVENT_START_AT_ACTION_ID",
    "FF_EVENT_START_DATE_ACTION_ID",
    "FF_EVENT_START_TIME_ACTION_ID",
    "PlanningCoordinator",
    "parse_draft_id_from_value",
]
